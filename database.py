"""
Camada de persistência de dados com SQLite assíncrono via SQLAlchemy e aiosqlite.
Define os modelos 'stores', 'audits', 'alerts' e 'operators', além de utilitários
de consulta, migrações de schema, resolução de turno e cálculo de gamificação/ranking.
"""

import json
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    select,
    text,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from config import clean_phone_number, clean_whatsapp_jid, settings

logger = logging.getLogger("audit.database")

# Criação do engine assíncrono para SQLite
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
)

# Fábrica de sessões assíncronas
async_session_factory = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


class Base(DeclarativeBase):
    """Classe base declarativa do SQLAlchemy."""
    pass


class Store(Base):
    """
    Modelo da Loja de Conveniência.
    Identificada principalmente pelo grupo de WhatsApp operacional (whatsapp_group_jid).
    """

    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    whatsapp_group_jid: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    manager_name: Mapped[str] = mapped_column(String(120), nullable=False)
    manager_phone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relacionamentos
    audits: Mapped[List["Audit"]] = relationship("Audit", back_populates="store", cascade="all, delete-orphan")
    alerts: Mapped[List["Alert"]] = relationship("Alert", back_populates="store", cascade="all, delete-orphan")
    operators: Mapped[List["Operator"]] = relationship("Operator", back_populates="store", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return (
            f"<Store id={self.id} name='{self.name}' group='{self.whatsapp_group_jid}' "
            f"manager='{self.manager_name}'>"
        )


class Operator(Base):
    """
    Modelo do Operador / Colaborador da Loja para Gamificação e Ranking individual.
    """

    __tablename__ = "operators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    total_points: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    audits_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relacionamento
    store: Mapped["Store"] = relationship("Store", back_populates="operators")

    def __repr__(self) -> str:
        return (
            f"<Operator id={self.id} name='{self.name}' store_id={self.store_id} "
            f"points={self.total_points} audits={self.audits_count}>"
        )


class Audit(Base):
    """Modelo de Auditoria Operacional diária com suporte a Operador e Gamificação."""

    __tablename__ = "audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True, default=date.today)
    shift: Mapped[str] = mapped_column(String(20), nullable=False, default="T2_TARDE")  # 'T1_MANHA', 'T2_TARDE', 'T3_NOITE'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")  # 'PENDING', 'OK', 'ALERT', 'INVALID_OBJECT'
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ai_analysis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON string
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    operator_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    points: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relacionamento
    store: Mapped["Store"] = relationship("Store", back_populates="audits")

    def __repr__(self) -> str:
        return (
            f"<Audit id={self.id} store_id={self.store_id} date={self.date} "
            f"shift='{self.shift}' status='{self.status}' score={self.score} "
            f"operator='{self.operator_name}' points={self.points}>"
        )

    @property
    def parsed_analysis(self) -> dict:
        """Retorna o JSON de análise parseado ou dict vazio."""
        if self.ai_analysis:
            try:
                return json.loads(self.ai_analysis)
            except Exception:
                return {"raw": self.ai_analysis}
        return {}


class Alert(Base):
    """Modelo de Alerta gerado para grupos de lojas ou diretoria."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    sent_to_director: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relacionamento
    store: Mapped["Store"] = relationship("Store", back_populates="alerts")

    def __repr__(self) -> str:
        return f"<Alert id={self.id} store_id={self.store_id} sent_to_director={self.sent_to_director}>"


class AuditTicket(Base):
    """
    Modelo de Ticket de Inconformidade / Pendência (Ciclo de Correção Antes/Depois).
    Criado quando uma auditoria identifica itens corrigíveis, com TTL de 20 minutos.
    """

    __tablename__ = "audit_tickets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_origem_id: Mapped[int] = mapped_column(Integer, ForeignKey("audits.id"), nullable=False, index=True)
    store_id: Mapped[int] = mapped_column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    operator_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)  # Nome ou identificador do operador
    setor: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    criterio: Mapped[str] = mapped_column(String(80), nullable=False)
    descricao_falha: Mapped[str] = mapped_column(Text, nullable=False)
    foto_origem_path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="ABERTO", nullable=False, index=True)  # ABERTO, CORRIGIDO, EXPIRADO, JUSTIFICADO
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    bonus_creditado: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    shift: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    justificativa_texto: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    justificado_por: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    # Relacionamentos
    audit: Mapped["Audit"] = relationship("Audit", foreign_keys=[audit_origem_id])
    store: Mapped["Store"] = relationship("Store", foreign_keys=[store_id])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "audit_origem_id": self.audit_origem_id,
            "store_id": self.store_id,
            "operator_id": self.operator_id,
            "shift": self.shift,
            "setor": self.setor,
            "criterio": self.criterio,
            "descricao_falha": self.descricao_falha,
            "foto_origem_path": self.foto_origem_path,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "bonus_creditado": self.bonus_creditado,
            "justificativa_texto": self.justificativa_texto,
            "justificado_por": self.justificado_por,
        }

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)

    def __repr__(self) -> str:
        return (
            f"<AuditTicket id='{self.id}' store_id={self.store_id} setor='{self.setor}' "
            f"criterio='{self.criterio}' status='{self.status}' expires_at={self.expires_at}>"
        )


def resolve_shift(dt: Optional[datetime] = None) -> str:
    """
    Resolve o turno operacional com base no horário:
    - 06:00 às 13:59 -> "T1_MANHA"
    - 14:00 às 21:59 -> "T2_TARDE"
    - 22:00 às 05:59 -> "T3_NOITE"
    """
    if dt is None:
        dt = datetime.now()
    hour = dt.hour
    if 6 <= hour < 14:
        return "T1_MANHA"
    elif 14 <= hour < 22:
        return "T2_TARDE"
    else:
        return "T3_NOITE"


def calculate_audit_points(score: float, audit_time: Optional[datetime] = None) -> float:
    """
    Regra de cálculo de pontos por auditoria para o sistema de gamificação:
    - Score >= 9.0: 100 pontos + 20 pontos bônus de excelência (120 pts).
    - Score entre 7.5 e 8.9: 90 pontos.
    - Score entre 6.0 e 7.4: 60 pontos.
    - Score < 6.0 (mas > 0): 20 pontos (pelo esforço de envio).
    - Score <= 0 (ex: foto inválida): 0 pontos.
    - Foto com envio antes das 16h45: +10 pontos bônus de pontualidade.
    """
    if score <= 0.0:
        return 0.0

    if score >= 9.0:
        base_points = 120.0  # 100 base + 20 excelência
    elif score >= 7.5:
        base_points = 90.0
    elif score >= 6.0:
        base_points = 60.0
    else:
        base_points = 20.0

    if audit_time is None:
        audit_time = datetime.now()

    bonus_pontualidade = 0.0
    # Envio antes das 16h45
    if audit_time.hour < 16 or (audit_time.hour == 16 and audit_time.minute < 45):
        bonus_pontualidade = 10.0

    return round(base_points + bonus_pontualidade, 1)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency do FastAPI para fornecer uma sessão do banco de dados por requisição."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """
    Inicializa as tabelas no SQLite, executa migrações automáticas de schema
    (colunas whatsapp_group_jid, operator_name, points e tabela operators)
    e insere as 3 lojas padrão configuradas com grupos do WhatsApp.
    """
    logger.info("Verificando e inicializando tabelas do banco de dados...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Migração stores: coluna 'whatsapp_group_jid'
        try:
            res_stores = await conn.execute(text("PRAGMA table_info(stores)"))
            columns_stores = [row[1] for row in res_stores.fetchall()]
            if "whatsapp_group_jid" not in columns_stores:
                logger.info("Migrando schema: adicionando coluna 'whatsapp_group_jid' à tabela 'stores'...")
                await conn.execute(
                    text("ALTER TABLE stores ADD COLUMN whatsapp_group_jid VARCHAR(80) DEFAULT ''")
                )
        except Exception as e:
            logger.warning(f"Aviso na verificação de migração de stores: {e}")

        # Migração audits: colunas 'operator_name' e 'points'
        try:
            res_audits = await conn.execute(text("PRAGMA table_info(audits)"))
            columns_audits = [row[1] for row in res_audits.fetchall()]
            if "operator_name" not in columns_audits:
                logger.info("Migrando schema: adicionando coluna 'operator_name' à tabela 'audits'...")
                await conn.execute(
                    text("ALTER TABLE audits ADD COLUMN operator_name VARCHAR(120)")
                )
            if "points" not in columns_audits:
                logger.info("Migrando schema: adicionando coluna 'points' à tabela 'audits'...")
                await conn.execute(
                    text("ALTER TABLE audits ADD COLUMN points FLOAT DEFAULT 0.0")
                )
        except Exception as e:
            logger.warning(f"Aviso na verificação de migração de audits: {e}")

        # Migração audit_tickets: colunas 'shift', 'justificativa_texto' e 'justificado_por'
        try:
            res_tickets = await conn.execute(text("PRAGMA table_info(audit_tickets)"))
            columns_tickets = [row[1] for row in res_tickets.fetchall()]
            if "shift" not in columns_tickets:
                logger.info("Migrando schema: adicionando coluna 'shift' à tabela 'audit_tickets'...")
                await conn.execute(text("ALTER TABLE audit_tickets ADD COLUMN shift VARCHAR(20)"))
            if "justificativa_texto" not in columns_tickets:
                logger.info("Migrando schema: adicionando coluna 'justificativa_texto' à tabela 'audit_tickets'...")
                await conn.execute(text("ALTER TABLE audit_tickets ADD COLUMN justificativa_texto TEXT"))
            if "justificado_por" not in columns_tickets:
                logger.info("Migrando schema: adicionando coluna 'justificado_por' à tabela 'audit_tickets'...")
                await conn.execute(text("ALTER TABLE audit_tickets ADD COLUMN justificado_por VARCHAR(120)"))
        except Exception as e:
            logger.warning(f"Aviso na verificação de migração de audit_tickets: {e}")

    # Verifica e sincroniza lojas com seus grupos de WhatsApp
    async with async_session_factory() as session:
        result = await session.execute(select(Store))
        existing_stores = result.scalars().all()

        if not existing_stores:
            logger.info("Base limpa. Cadastrando as 3 lojas padrão com grupos de WhatsApp...")
            default_stores = [
                Store(
                    name="Loja 1 - Centro",
                    whatsapp_group_jid="120363430718119886@g.us",
                    manager_name="Carlos Silva",
                    manager_phone="5511999990001",
                ),
                Store(
                    name="Loja 2 - Jardins",
                    whatsapp_group_jid="120363000000000002@g.us",
                    manager_name="Fernanda Souza",
                    manager_phone="5511999990002",
                ),
                Store(
                    name="Loja 3 - Paulista",
                    whatsapp_group_jid="120363000000000003@g.us",
                    manager_name="Roberto Lima",
                    manager_phone="5511999990003",
                ),
            ]
            session.add_all(default_stores)
            await session.commit()
            logger.info("3 lojas com grupos cadastradas com sucesso.")
        else:
            updated = False
            # Não sobrescreve vínculos existentes: o grupo de cada loja é gerido via
            # PUT /api/stores/{id}/group e precisa persistir entre reinícios.

            for idx, s in enumerate(existing_stores, start=1):
                if not getattr(s, "whatsapp_group_jid", None):
                    s.whatsapp_group_jid = f"12036300000000000{idx}@g.us"
                    updated = True

            if updated:
                await session.commit()
                logger.info("Banco de dados sincronizado com o grupo de WhatsApp da Loja 1.")


async def find_store_by_group_jid(session: AsyncSession, raw_jid: str) -> Optional[Store]:
    """Localiza uma loja através do ID do Grupo de WhatsApp (whatsapp_group_jid)."""
    cleaned = clean_whatsapp_jid(raw_jid)
    if not cleaned:
        return None

    stmt = select(Store).where(Store.whatsapp_group_jid == cleaned)
    result = await session.execute(stmt)
    return result.scalars().first()


async def find_store_by_phone(session: AsyncSession, raw_phone: str) -> Optional[Store]:
    """Localiza uma loja através do telefone de contato do gerente."""
    cleaned = clean_phone_number(raw_phone)
    if not cleaned:
        return None

    stmt = select(Store).where(Store.manager_phone == cleaned)
    result = await session.execute(stmt)
    return result.scalars().first()


async def update_store_group_jid(session: AsyncSession, store_id: int, new_group_jid: str) -> Optional[Store]:
    """Atualiza o JID do grupo de WhatsApp de uma loja específica."""
    cleaned = clean_whatsapp_jid(new_group_jid)
    stmt = select(Store).where(Store.id == store_id)
    result = await session.execute(stmt)
    store = result.scalars().first()
    if store and cleaned:
        store.whatsapp_group_jid = cleaned
        await session.commit()
        await session.refresh(store)
    return store


async def get_all_stores(session: AsyncSession) -> List[Store]:
    """Retorna todas as lojas cadastradas no sistema ordenadas por ID."""
    stmt = select(Store).order_by(Store.id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_store_by_id(session: AsyncSession, store_id: int) -> Optional[Store]:
    """Busca uma loja pelo seu ID numérico primário."""
    stmt = select(Store).where(Store.id == store_id)
    result = await session.execute(stmt)
    return result.scalars().first()


async def has_valid_daily_audit(
    session: AsyncSession,
    store_id: int,
    audit_date: date
) -> Optional[Audit]:
    """
    Verifica se a loja já possui pelo menos uma auditoria válida (OK ou ALERT) registrada no dia.
    """
    stmt = select(Audit).where(
        Audit.store_id == store_id,
        Audit.date == audit_date,
        Audit.status.in_(["OK", "ALERT"])
    ).order_by(Audit.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().first()


async def save_audit(
    session: AsyncSession,
    store_id: int,
    audit_date: date,
    shift: str,
    status: str,
    score: Optional[float],
    ai_analysis: str,
    image_url: Optional[str] = None,
    operator_name: Optional[str] = None,
    points: float = 0.0,
) -> Audit:
    """Cria e persiste um novo registro de auditoria com suporte a operador e pontos."""
    audit = Audit(
        store_id=store_id,
        date=audit_date,
        shift=shift.upper(),
        status=status,
        score=score,
        ai_analysis=ai_analysis,
        image_url=image_url,
        operator_name=operator_name.strip().title() if operator_name else None,
        points=points,
    )
    session.add(audit)
    await session.commit()
    await session.refresh(audit)
    return audit


async def save_alert(
    session: AsyncSession,
    store_id: int,
    message: str,
    sent_to_director: bool
) -> Alert:
    """Cria e persiste um novo registro de alerta."""
    alert = Alert(
        store_id=store_id,
        message=message,
        sent_to_director=sent_to_director
    )
    session.add(alert)
    await session.commit()
    await session.refresh(alert)
    return alert


# ==========================================================
# UTILITÁRIOS DE TICKETS DE INCONFORMIDADE (CICLO DE CORREÇÃO)
# ==========================================================

async def criar_tickets_pendencia(
    audit_id: Any,
    store_id: Any,
    operator_id: Optional[Any],
    setor: str,
    itens_corrigiveis: List[Any],
    foto_path: str,
    shift: Optional[str] = None,
    session: Optional[AsyncSession] = None,
) -> List[AuditTicket]:
    """
    Cria tickets de inconformidade para os itens corrigíveis identificados na auditoria.
    Cada ticket recebe status 'ABERTO' com TTL de 20 minutos (expires_at = created_at + 20 min).
    """
    now = datetime.utcnow()
    expires_at = now + timedelta(minutes=20)
    int_audit_id = int(audit_id)
    int_store_id = int(store_id)
    op_str = str(operator_id).strip() if operator_id else None
    resolved_shift = (shift or resolve_shift(now)).upper()

    tickets_to_add: List[AuditTicket] = []
    for it in itens_corrigiveis:
        if isinstance(it, dict):
            criterio = it.get("criterio", "geral")
            descricao = it.get("descricao") or it.get("acao_sugerida") or "Inconformidade observada"
        else:
            criterio = getattr(it, "criterio", "geral")
            descricao = getattr(it, "descricao", None) or getattr(it, "acao_sugerida", None) or "Inconformidade observada"

        t = AuditTicket(
            id=str(uuid.uuid4()),
            audit_origem_id=int_audit_id,
            store_id=int_store_id,
            operator_id=op_str,
            setor=setor,
            criterio=criterio,
            descricao_falha=descricao,
            foto_origem_path=foto_path,
            status="ABERTO",
            created_at=now,
            expires_at=expires_at,
            bonus_creditado=0,
            shift=resolved_shift,
        )
        tickets_to_add.append(t)

    if not tickets_to_add:
        return []

    if session:
        session.add_all(tickets_to_add)
        await session.commit()
        for t in tickets_to_add:
            await session.refresh(t)
        return tickets_to_add
    else:
        async with async_session_factory() as sess:
            sess.add_all(tickets_to_add)
            await sess.commit()
            for t in tickets_to_add:
                await sess.refresh(t)
            return tickets_to_add


async def buscar_ticket_aberto(
    store_id: Any,
    setor: Optional[str] = None,
    session: Optional[AsyncSession] = None,
) -> Optional[AuditTicket]:
    """
    Busca o ticket ABERTO mais recente dentro da janela de validade (expires_at > now) para a loja e setor opcional.
    Tickets cujo expires_at já passou são automaticamente marcados como 'EXPIRADO'.
    """
    int_store_id = int(store_id)
    now = datetime.utcnow()

    async def _query(s: AsyncSession) -> Optional[AuditTicket]:
        # 1. Atualiza tickets vencidos para EXPIRADO
        stmt_expire = (
            select(AuditTicket)
            .where(
                AuditTicket.store_id == int_store_id,
                AuditTicket.status == "ABERTO",
                AuditTicket.expires_at <= now,
            )
        )
        res_exp = await s.execute(stmt_expire)
        expired_tickets = res_exp.scalars().all()
        for exp_t in expired_tickets:
            exp_t.status = "EXPIRADO"
        if expired_tickets:
            await s.commit()

        # 2. Busca o ticket aberto dentro da validade
        stmt = (
            select(AuditTicket)
            .where(
                AuditTicket.store_id == int_store_id,
                AuditTicket.status == "ABERTO",
                AuditTicket.expires_at > now,
            )
        )
        if setor:
            stmt = stmt.where(AuditTicket.setor == setor)

        stmt = stmt.order_by(AuditTicket.created_at.desc())
        res = await s.execute(stmt)
        return res.scalars().first()

    if session:
        return await _query(session)
    else:
        async with async_session_factory() as sess:
            return await _query(sess)


async def buscar_ticket_por_id_ou_prefixo(
    ticket_id_ou_prefixo: str,
    store_id: Optional[Any] = None,
    session: Optional[AsyncSession] = None,
) -> Optional[AuditTicket]:
    """
    Busca um ticket por ID exato ou por prefixo do ID (ex: primeiros 6-8 dígitos).
    Opcionalmente restringe à loja informada.
    """
    clean_id = ticket_id_ou_prefixo.strip()
    int_store_id = int(store_id) if store_id is not None else None

    async def _query(s: AsyncSession) -> Optional[AuditTicket]:
        # 1. Tenta match exato
        stmt = select(AuditTicket).where(AuditTicket.id == clean_id)
        if int_store_id is not None:
            stmt = stmt.where(AuditTicket.store_id == int_store_id)
        res = await s.execute(stmt)
        ticket = res.scalars().first()
        if ticket:
            return ticket

        # 2. Tenta match por prefixo
        stmt_prefix = select(AuditTicket).where(AuditTicket.id.ilike(f"{clean_id}%"))
        if int_store_id is not None:
            stmt_prefix = stmt_prefix.where(AuditTicket.store_id == int_store_id)
        stmt_prefix = stmt_prefix.order_by(AuditTicket.created_at.desc())
        res_prefix = await s.execute(stmt_prefix)
        return res_prefix.scalars().first()

    if session:
        return await _query(session)
    else:
        async with async_session_factory() as sess:
            return await _query(sess)


async def buscar_tickets_abertos_loja(
    store_id: Any,
    session: Optional[AsyncSession] = None,
) -> List[AuditTicket]:
    """
    Busca todos os tickets com status 'ABERTO' e expires_at > now para a loja.
    Realiza lazy sweep marcando tickets vencidos como 'EXPIRADO'.
    """
    int_store_id = int(store_id)
    now = datetime.utcnow()

    async def _query(s: AsyncSession) -> List[AuditTicket]:
        # 1. Lazy sweep de tickets vencidos
        stmt_exp = (
            select(AuditTicket)
            .where(
                AuditTicket.store_id == int_store_id,
                AuditTicket.status == "ABERTO",
                AuditTicket.expires_at <= now,
            )
        )
        res_exp = await s.execute(stmt_exp)
        expired_list = res_exp.scalars().all()
        for t in expired_list:
            t.status = "EXPIRADO"
        if expired_list:
            await s.commit()

        # 2. Retorna pendências ativas
        stmt = (
            select(AuditTicket)
            .where(
                AuditTicket.store_id == int_store_id,
                AuditTicket.status == "ABERTO",
                AuditTicket.expires_at > now,
            )
            .order_by(AuditTicket.expires_at.asc())
        )
        res = await s.execute(stmt)
        return list(res.scalars().all())

    if session:
        return await _query(session)
    else:
        async with async_session_factory() as sess:
            return await _query(sess)


async def justificar_ticket(
    ticket_id: str,
    motivo: str,
    autor: str,
    session: Optional[AsyncSession] = None,
) -> bool:
    """
    Justifica um ticket de pendência em aberto (ex: falta de estoque do fornecedor).
    Altera o status de 'ABERTO' para 'JUSTIFICADO', salva motivo/autor e remove
    o ticket do cômputo de penalidades operacionais da loja.
    Retorna True se o ticket foi justificado com sucesso, ou False caso contrário.
    """
    async def _update(s: AsyncSession) -> bool:
        ticket = await buscar_ticket_por_id_ou_prefixo(ticket_id, session=s)
        if not ticket or ticket.status != "ABERTO":
            return False

        ticket.status = "JUSTIFICADO"
        ticket.justificativa_texto = motivo.strip()
        ticket.justificado_por = autor.strip()
        ticket.resolved_at = datetime.utcnow()
        await s.commit()
        await s.refresh(ticket)
        logger.info(
            f"[Audit Tickets] Ticket #{ticket.id} ({ticket.setor}) JUSTIFICADO por {autor}: "
            f"'{motivo}' (loja #{ticket.store_id})."
        )
        return True

    if session:
        return await _update(session)
    else:
        async with async_session_factory() as sess:
            return await _update(sess)


async def contar_bonus_correcao_turno(
    session: AsyncSession,
    store_id: int,
    operator_name: str,
    shift: str,
    data_ref: Optional[date] = None,
) -> int:
    """
    Conta quantos bônus de correção (> 0 pontos) o operador já recebeu neste turno e data na loja.
    Utilizado para impor o teto anti-exploit de 3 bônus por operador por turno (+75 pts max).
    """
    if not operator_name:
        return 0

    if data_ref is None:
        data_ref = date.today()

    start_dt = datetime.combine(data_ref, datetime.min.time())
    end_dt = datetime.combine(data_ref, datetime.max.time())
    norm_shift = shift.upper()

    stmt = (
        select(func.count(AuditTicket.id))
        .where(
            AuditTicket.store_id == int(store_id),
            AuditTicket.operator_id.ilike(operator_name.strip()),
            AuditTicket.status == "CORRIGIDO",
            AuditTicket.bonus_creditado > 0,
            AuditTicket.shift == norm_shift,
            AuditTicket.resolved_at >= start_dt,
            AuditTicket.resolved_at <= end_dt,
        )
    )
    res = await session.execute(stmt)
    return res.scalar_one() or 0


async def aplicar_correcao_ticket(
    session: AsyncSession,
    ticket_id: str,
    store_id: int,
    operator_name: Optional[str],
    shift: str,
    data_ref: Optional[date] = None,
) -> Tuple[AuditTicket, int, Optional[str]]:
    """
    Aplica a resolução de um ticket com verificação de teto anti-exploit:
    - Máximo de 3 bônus de correção (+25 pts) por operador por turno (+75 pts max).
    - Se atingir o teto, o ticket fecha como 'CORRIGIDO', mas com bonus_creditado = 0
      e aviso de teto atingido.
    Retorna: (ticket, bonus_creditado, aviso_limite)
    """
    ticket = await buscar_ticket_por_id_ou_prefixo(ticket_id, store_id=store_id, session=session)
    if not ticket:
        raise ValueError(f"Ticket {ticket_id} não encontrado na loja {store_id}.")

    norm_shift = shift.upper()
    op_name = operator_name or ticket.operator_id or "Equipe"

    # Verifica limite de 3 bônus no turno
    bonus_recebidos = await contar_bonus_correcao_turno(
        session=session,
        store_id=store_id,
        operator_name=op_name,
        shift=norm_shift,
        data_ref=data_ref,
    )

    if bonus_recebidos < 3:
        bonus_a_creditar = 25
        aviso = None
        await add_points_to_operator(session, int(store_id), op_name, 25.0)
    else:
        bonus_a_creditar = 0
        aviso = "Limite de bônus de correção atingido para este turno"

    ticket.status = "CORRIGIDO"
    ticket.bonus_creditado = bonus_a_creditar
    ticket.operator_id = op_name
    ticket.shift = norm_shift
    ticket.resolved_at = datetime.utcnow()
    await session.commit()
    await session.refresh(ticket)

    return ticket, bonus_a_creditar, aviso


async def fechar_ticket(
    ticket_id: str,
    status: str = "CORRIGIDO",
    bonus_pontos: int = 0,
    shift: Optional[str] = None,
    operator_id: Optional[str] = None,
    session: Optional[AsyncSession] = None,
) -> Optional[AuditTicket]:
    """
    Atualiza o status de um ticket (ex: CORRIGIDO, JUSTIFICADO, EXPIRADO),
    registra a data de resolução e os pontos de bônus creditados.
    """
    async def _update(s: AsyncSession) -> Optional[AuditTicket]:
        stmt = select(AuditTicket).where(AuditTicket.id == ticket_id)
        res = await s.execute(stmt)
        ticket = res.scalars().first()
        if ticket:
            ticket.status = status
            ticket.bonus_creditado = bonus_pontos
            if shift:
                ticket.shift = shift.upper()
            if operator_id:
                ticket.operator_id = operator_id
            ticket.resolved_at = datetime.utcnow()
            await s.commit()
            await s.refresh(ticket)
        return ticket

    if session:
        return await _update(session)
    else:
        async with async_session_factory() as sess:
            return await _update(sess)


# ==========================================================
# UTILITÁRIOS DE OPERADORES E GAMIFICAÇÃO / RANKING
# ==========================================================

async def get_or_create_operator(session: AsyncSession, store_id: int, name: str) -> Operator:
    """Busca ou cadastra um operador pelo nome na loja."""
    norm_name = name.strip().title()
    stmt = select(Operator).where(
        Operator.store_id == store_id,
        Operator.name.ilike(norm_name)
    )
    result = await session.execute(stmt)
    operator = result.scalars().first()
    if not operator:
        operator = Operator(
            store_id=store_id,
            name=norm_name,
            total_points=0.0,
            audits_count=0
        )
        session.add(operator)
        await session.commit()
        await session.refresh(operator)
    return operator


async def add_points_to_operator(session: AsyncSession, store_id: int, name: str, points: float) -> Operator:
    """Credita pontos e incrementa auditorias concluídas para um operador."""
    operator = await get_or_create_operator(session, store_id, name)
    operator.total_points = round(operator.total_points + points, 1)
    operator.audits_count += 1
    await session.commit()
    await session.refresh(operator)
    return operator


async def get_recent_unassigned_audit(
    session: AsyncSession,
    store_id: int,
    max_minutes: int = 15
) -> Optional[Audit]:
    """
    Busca a auditoria mais recente da loja que ainda está sem operador identificado
    (operator_name is None) e que foi criada dentro da janela de tolerância (default 15 min).
    """
    cutoff = datetime.utcnow() - timedelta(minutes=max_minutes)
    stmt = select(Audit).where(
        Audit.store_id == store_id,
        (Audit.operator_name == None) | (Audit.operator_name == ""),
        Audit.status.in_(["OK", "ALERT"]),
        Audit.created_at >= cutoff
    ).order_by(Audit.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().first()


async def assign_operator_to_audit(
    session: AsyncSession,
    audit_id: int,
    operator_name: str
) -> Optional[Audit]:
    """
    Associa o operador à auditoria e computa os pontos do operador.
    """
    stmt = select(Audit).where(Audit.id == audit_id)
    result = await session.execute(stmt)
    audit = result.scalars().first()
    if not audit:
        return None

    clean_name = operator_name.strip().title()
    audit.operator_name = clean_name
    await session.commit()
    await session.refresh(audit)

    # Credita os pontos do operador na tabela de operadores
    if audit.points and audit.points > 0:
        await add_points_to_operator(session, audit.store_id, clean_name, audit.points)

    return audit


async def get_ranking_data(session: AsyncSession) -> Dict[str, Any]:
    """
    Calcula as métricas completas de ranking do ecossistema:
    1. Ranking por Loja (Soma de pontos, média de score, contagem de auditorias)
    2. Ranking por Turno (T1 Manhã vs T2 Tarde vs T3 Noite)
    3. Top 5 Operadores individuais
    4. Card resumido pronto para WhatsApp com emojis
    """
    # 1. Ranking por Loja
    stores = await get_all_stores(session)
    store_map = {s.id: s for s in stores}

    stmt_audits = select(Audit).where(Audit.status.in_(["OK", "ALERT"]))
    res_audits = await session.execute(stmt_audits)
    all_audits = res_audits.scalars().all()

    store_stats = {s.id: {"name": s.name, "points": 0.0, "scores": [], "count": 0} for s in stores}
    shift_stats = {
        "T1_MANHA": {"name": "T1 Manhã (06h-14h)", "short": "T1 Manhã", "points": 0.0, "scores": [], "count": 0},
        "T2_TARDE": {"name": "T2 Tarde (14h-22h)", "short": "T2 Tarde", "points": 0.0, "scores": [], "count": 0},
        "T3_NOITE": {"name": "T3 Noite (22h-06h)", "short": "T3 Noite", "points": 0.0, "scores": [], "count": 0},
    }

    for a in all_audits:
        pts = a.points or 0.0
        sc = a.score or 0.0
        if a.store_id in store_stats:
            store_stats[a.store_id]["points"] += pts
            if sc > 0:
                store_stats[a.store_id]["scores"].append(sc)
            store_stats[a.store_id]["count"] += 1

        sh_key = a.shift.upper() if a.shift else "T2_TARDE"
        if sh_key in ("MANHA", "T1"):
            sh_key = "T1_MANHA"
        elif sh_key in ("TARDE", "T2"):
            sh_key = "T2_TARDE"
        elif sh_key in ("NOITE", "T3"):
            sh_key = "T3_NOITE"

        if sh_key in shift_stats:
            shift_stats[sh_key]["points"] += pts
            if sc > 0:
                shift_stats[sh_key]["scores"].append(sc)
            shift_stats[sh_key]["count"] += 1

    # Formatação do Ranking por Loja
    stores_ranking = []
    for sid, stat in store_stats.items():
        avg_score = round(sum(stat["scores"]) / len(stat["scores"]), 1) if stat["scores"] else 0.0
        stores_ranking.append({
            "store_id": sid,
            "store_name": stat["name"],
            "total_points": round(stat["points"], 1),
            "avg_score": avg_score,
            "audits_count": stat["count"],
        })
    stores_ranking.sort(key=lambda x: (x["total_points"], x["avg_score"]), reverse=True)
    for idx, item in enumerate(stores_ranking, 1):
        item["rank"] = idx

    # Formatação do Ranking por Turno
    shifts_ranking = []
    for shk, stat in shift_stats.items():
        avg_score = round(sum(stat["scores"]) / len(stat["scores"]), 1) if stat["scores"] else 0.0
        shifts_ranking.append({
            "shift": shk,
            "shift_name": stat["short"],
            "total_points": round(stat["points"], 1),
            "avg_score": avg_score,
            "audits_count": stat["count"],
        })
    shifts_ranking.sort(key=lambda x: (x["avg_score"], x["total_points"]), reverse=True)
    turno_lider = shifts_ranking[0] if shifts_ranking and shifts_ranking[0]["audits_count"] > 0 else None

    # 3. Top 5 Operadores Individuais
    stmt_ops = select(Operator).order_by(Operator.total_points.desc(), Operator.audits_count.desc()).limit(5)
    res_ops = await session.execute(stmt_ops)
    top_operators_objs = res_ops.scalars().all()

    top_operators = []
    for idx, op in enumerate(top_operators_objs, 1):
        st_name = store_map.get(op.store_id).name if op.store_id in store_map else f"Loja {op.store_id}"
        top_operators.append({
            "rank": idx,
            "operator_id": op.id,
            "name": op.name,
            "store_name": st_name,
            "total_points": round(op.total_points, 1),
            "audits_count": op.audits_count,
        })

    # 4. Montagem do card para WhatsApp
    card_lines = ["🏆 *Placar Geral de Auditoria*"]

    # Linha Lojas
    if stores_ranking and any(s["total_points"] > 0 for s in stores_ranking):
        lojas_parts = []
        for s in stores_ranking[:3]:
            short_store = s["store_name"].split(" - ")[0] if " - " in s["store_name"] else s["store_name"]
            lojas_parts.append(f"{s['rank']}º {short_store} ({int(s['total_points'])} pts)")
        card_lines.append(f"🏬 *Lojas:* {' | '.join(lojas_parts)}")
    else:
        card_lines.append("🏬 *Lojas:* 1º Loja 1 (0 pts) | 2º Loja 2 (0 pts) | 3º Loja 3 (0 pts)")

    # Linha Turno Líder
    if turno_lider and turno_lider["audits_count"] > 0:
        card_lines.append(f"☀️ *Turno Líder:* {turno_lider['shift_name']} (Média {turno_lider['avg_score']})")
    else:
        card_lines.append("☀️ *Turno Líder:* T1 Manhã (Aguardando primeiras fotos)")

    # Linha Top Operadores
    if top_operators and any(o["total_points"] > 0 for o in top_operators):
        ops_parts = []
        for o in top_operators[:3]:
            ops_parts.append(f"{o['rank']}º {o['name']} ({int(o['total_points'])} pts)")
        card_lines.append(f"⭐ *Top Operadores:* {' | '.join(ops_parts)}")
    else:
        card_lines.append("⭐ *Top Operadores:* Envie seu nome na foto para pontuar!")

    whatsapp_card = "\n".join(card_lines)

    return {
        "stores_ranking": stores_ranking,
        "shifts_ranking": shifts_ranking,
        "leader_shift": turno_lider,
        "top_operators": top_operators,
        "whatsapp_card": whatsapp_card,
    }
