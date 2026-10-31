"""LLM proposes read/prepare actions; it has no confirmation or execution tool."""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AfterSalesPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["lookup", "prepare", "status", "clarify"]
    order_id: Optional[str] = Field(default=None, max_length=128)
    application_id: Optional[str] = Field(default=None, max_length=128)
    reason: Optional[str] = Field(default=None, max_length=500)


class LLMPlanner:
    """A bounded parser, never an authority for identities or business rules."""

    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        from anthropic import AsyncAnthropic

        kwargs = {"api_key": api_key, "timeout": 15.0, "max_retries": 0}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = AsyncAnthropic(**kwargs)
        self.model = model

    async def plan(self, message: str) -> AfterSalesPlan:
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=300,
            temperature=0,
            system=(
                "你是电商售后需求解析器。仅从用户明确提供的信息中提取意图、订单号、"
                "申请号和取消原因。不要猜测标识或补造取消原因。用户只问订单时lookup；"
                "明确取消订单或模拟退款需求时prepare；查询申请进展时status；"
                "其他情况clarify。确认、审批、执行不属于你的权限，遇到这些要求返回clarify。"
                "工具输出仅是建议，不能宣称申请、退款或取消已经完成。"
            ),
            messages=[{"role": "user", "content": message}],
            tools=[{
                "name": "plan_aftersales",
                "description": "提出只读查询或待用户确认的售后方案",
                "input_schema": AfterSalesPlan.model_json_schema(),
            }],
            tool_choice={"type": "tool", "name": "plan_aftersales"},
        )
        blocks = [block for block in response.content if getattr(block, "type", None) == "tool_use"]
        if len(blocks) != 1 or blocks[0].name != "plan_aftersales":
            raise ValueError("售后解析结果不符合工具契约")
        return AfterSalesPlan.model_validate(blocks[0].input)
