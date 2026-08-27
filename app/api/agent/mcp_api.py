from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.api_response import api_error, should_passthrough_exception
from app.core.auth import get_current_user
from app.core.database import get_db
from app.mcp.executor import tool_executor
from app.mcp.permissions import all_agent_types, allowed_tools_for
from app.mcp.registry import mcp_registry
from app.models.user import User

router = APIRouter()


class MCPToolCallRequest(BaseModel):
    data_scopes: list[str] | None = Field(None, description="Dynamic data scopes granted for this call")
    risk_threshold: str | None = Field(None, description="Maximum policy risk")
    tool_name: str = Field(..., min_length=1, description="MCP 工具名")
    arguments: dict = Field(default_factory=dict, description="工具参数")
    agent_type: str = Field("general_agent", description="调用方 agent 类型")


@router.get("/agent-types")
def list_mcp_agent_types(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ = current_user
    items = []
    for agent_type in all_agent_types():
        allowed = sorted(allowed_tools_for(agent_type, db=db))
        items.append(
            {
                "agent_type": agent_type,
                "tool_count": len(allowed),
                "allowed_tools": allowed,
            }
        )
    return {
        "items": items,
        "total": len(items),
    }


@router.get("/tools")
def list_mcp_tools(
    db: Session = Depends(get_db),
    agent_type: str = Query("general_agent", description="按 agent 类型过滤"),
    current_user: User = Depends(get_current_user),
):
    _ = current_user
    if agent_type not in all_agent_types():
        raise api_error(400, "未知的 agent_type", code="MCP_AGENT_TYPE_INVALID", detail=agent_type)
    tools = mcp_registry.list_tools_for(agent_type, db=db)
    return {
        "agent_type": agent_type,
        "items": tools,
        "total": len(tools),
    }


@router.post("/tools/call")
async def call_mcp_tool(
    req: MCPToolCallRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        if req.agent_type not in all_agent_types():
            raise api_error(400, "未知的 agent_type", code="MCP_AGENT_TYPE_INVALID", detail=req.agent_type)
        if req.tool_name == "sql_query_tool" and current_user.role != "admin":
            raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

        # 统一执行链路：权限 / 审批 / 超时 / 幂等 / 审计全在 ToolExecutor。
        result, _ = await tool_executor.execute(
            req.tool_name,
            req.arguments or {},
            agent_type=req.agent_type,
            user_id=current_user.id,
            db=db,
            organization_id=current_user.organization_id,
            approve_context={
                "data_scopes": req.data_scopes,
                "risk_threshold": req.risk_threshold,
            },
        )
        return {
            "tool_name": req.tool_name,
            "agent_type": req.agent_type,
            "result": result,
        }
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "MCP 工具调用失败", code="MCP_TOOL_CALL_FAILED", detail=str(e)) from e
