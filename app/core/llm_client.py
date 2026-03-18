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
) -> str:
    """
    呼叫 Azure OpenAI，使用三明治結構 Prompt。

    Args:
        user_message: 使用者輸入
        conversation_history: 對話歷史
            [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
        security_hint: 傳入 "HIGH_RISK" 時將在系統提示注入安全提示層（Phase 5b WARN 用）
    """
    client = get_azure_client()
    deployment = os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"]

    # 建構訊息列表
    # System message 為頂層 + [選配安全提示] + 底層護欄
    system_message = {
        "role": "system",
        "content": build_prompt("", security_hint=security_hint),
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
