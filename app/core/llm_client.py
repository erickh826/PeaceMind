"""
LLM Core — Azure OpenAI 客戶端
使用 openai SDK 連接 Azure OpenAI (GPT-4o)
"""

import os
from openai import AzureOpenAI
from app.prompts.system_prompt import build_prompt


def get_azure_client() -> AzureOpenAI:
    return AzureOpenAI(
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    )


def chat_with_llm(
    user_message: str,
    conversation_history: list[dict] | None = None,
    security_hint: str | None = None,
    persona_name: str = "Boon",
    persona_fragment: str | None = None,
    profile_text: str | None = None,
    past_summaries_text: str | None = None,
) -> str:
    """
    呼叫 Azure OpenAI，使用三明治結構 Prompt。

    Args:
        user_message: 使用者輸入
        conversation_history: 對話歷史
            [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
        security_hint: 傳入 "HIGH_RISK" 時將在系統提示注入安全提示層（Phase 5b WARN 用）
        persona_name: 目前使用的 persona 名稱（Phase 1 起可替換，預設 "Boon"）
        persona_fragment: 目前使用的 persona 人格描述片段；不傳則使用預設「Boon」內容
        profile_text: Phase 2 Context Assembly Service 組好的學生 Profile 文字區塊
        past_summaries_text: Phase 2 Context Assembly Service 組好的跨 session 摘要文字區塊
    """
    client = get_azure_client()
    deployment = os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"]

    # 建構訊息列表
    # System message 為 [語言限制+persona+context] + SAFETY_CORE + [選配安全提示] + 底層護欄
    prompt_kwargs = {
        "security_hint": security_hint,
        "persona_name": persona_name,
        "profile_text": profile_text,
        "past_summaries_text": past_summaries_text,
    }
    if persona_fragment is not None:
        prompt_kwargs["persona_fragment"] = persona_fragment

    system_message = {
        "role": "system",
        "content": build_prompt("", **prompt_kwargs),
    }

    messages = [system_message]

    # 加入對話歷史（如有）
    if conversation_history:
        messages.extend(conversation_history)

    # 最新用戶訊息（使用 XML 標籤隔離）
    messages.append({
        "role": "user",
        "content": f"<user_input>\n{user_message}\n</user_input>",
    })

    response = client.chat.completions.create(
        model=deployment,
        messages=messages,
        temperature=0.7,
        max_tokens=800,
    )

    return response.choices[0].message.content or ""
