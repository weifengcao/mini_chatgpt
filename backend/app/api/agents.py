from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_admin
from app.models import Agent, AgentAccessAssignment, User
from app.schemas import AgentAccessUpdate, AgentCreate, AgentOut, AgentUpdate
from app.services import user_can_access_agent

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("", response_model=list[AgentOut])
def list_agents(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[Agent]:
    query = select(Agent).where(Agent.company_id == user.company_id, Agent.status != "deleted")
    agents = list(db.scalars(query.order_by(Agent.created_at.desc())))
    if user.role == "SUPER_ADMIN":
        return agents
    return [agent for agent in agents if user_can_access_agent(db, user, agent.id)]


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
def create_agent(payload: AgentCreate, db: Session = Depends(get_db), user: User = Depends(require_admin)) -> Agent:
    active_count = db.scalar(
        select(func.count(Agent.id)).where(Agent.company_id == user.company_id, Agent.status == "active")
    )
    if active_count >= 20:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="AGENT_LIMIT_EXCEEDED")
    agent = Agent(
        company_id=user.company_id,
        name=payload.name,
        description=payload.description,
        system_prompt=payload.system_prompt,
        model_provider=payload.model_provider,
        model_name=payload.model_name,
        tool_allowlist_json=payload.tool_allowlist_json,
        created_by=user.id,
    )
    db.add(agent)
    db.flush()
    db.add(AgentAccessAssignment(company_id=user.company_id, agent_id=agent.id, user_id=user.id, granted_by=user.id))
    db.commit()
    db.refresh(agent)
    return agent


@router.get("/{agent_id}", response_model=AgentOut)
def get_agent(agent_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Agent:
    agent = db.get(Agent, agent_id)
    if agent is None or agent.company_id != user.company_id or agent.status == "deleted":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if not user_can_access_agent(db, user, agent.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent access required")
    return agent


@router.patch("/{agent_id}", response_model=AgentOut)
def update_agent(
    agent_id: str,
    payload: AgentUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> Agent:
    agent = db.get(Agent, agent_id)
    if agent is None or agent.company_id != user.company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(agent, key, value)
    db.commit()
    db.refresh(agent)
    return agent


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_agent(agent_id: str, db: Session = Depends(get_db), user: User = Depends(require_admin)) -> None:
    agent = db.get(Agent, agent_id)
    if agent is None or agent.company_id != user.company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    agent.status = "deleted"
    db.commit()


@router.get("/{agent_id}/access", response_model=list[str])
def get_agent_access(agent_id: str, db: Session = Depends(get_db), user: User = Depends(require_admin)) -> list[str]:
    agent = db.get(Agent, agent_id)
    if agent is None or agent.company_id != user.company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return list(
        db.scalars(
            select(AgentAccessAssignment.user_id).where(
                AgentAccessAssignment.company_id == user.company_id,
                AgentAccessAssignment.agent_id == agent_id,
            )
        )
    )


@router.put("/{agent_id}/access", response_model=list[str])
def put_agent_access(
    agent_id: str,
    payload: AgentAccessUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> list[str]:
    agent = db.get(Agent, agent_id)
    if agent is None or agent.company_id != user.company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    db.execute(
        delete(AgentAccessAssignment).where(
            AgentAccessAssignment.company_id == user.company_id,
            AgentAccessAssignment.agent_id == agent_id,
        )
    )
    valid_users = list(db.scalars(select(User.id).where(User.company_id == user.company_id, User.id.in_(payload.user_ids))))
    for user_id in valid_users:
        db.add(AgentAccessAssignment(company_id=user.company_id, agent_id=agent_id, user_id=user_id, granted_by=user.id))
    db.commit()
    return valid_users
