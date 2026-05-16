from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Agent, AgentAccessAssignment, Company, User
from app.security import hash_password


def seed_demo_data(db: Session, settings: Settings) -> None:
    existing = db.scalar(select(User).where(User.email == settings.demo_admin_email))
    if existing is not None:
        return

    company = Company(name="Demo Company")
    db.add(company)
    db.flush()

    admin = User(
        company_id=company.id,
        email=settings.demo_admin_email,
        password_hash=hash_password(settings.demo_admin_password),
        role="SUPER_ADMIN",
    )
    db.add(admin)
    db.flush()

    agent = Agent(
        company_id=company.id,
        name="Demo Agent",
        description="Default assistant with calculator access.",
        system_prompt="You are a helpful assistant inside mini_chatgpt. Answer clearly and concisely.",
        model_provider=settings.ai_provider,
        model_name=settings.ai_model,
        tool_allowlist_json=["calculator"],
        created_by=admin.id,
    )
    db.add(agent)
    db.flush()

    db.add(AgentAccessAssignment(company_id=company.id, agent_id=agent.id, user_id=admin.id, granted_by=admin.id))
    db.commit()
