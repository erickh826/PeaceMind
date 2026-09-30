"""
共用的「條件 JSON 比對」邏輯（Phase 3 從 persona_resolver.py 抽出）

persona_resolver.py（persona_match_conditions）、example_selector.py
（response_examples.applicable_conditions_json），以及未來 Phase 4 的
rule_engine.py（rules.conditions_json）都從這裡 import，避免同一套比對規則
（主題門檻、未知條件鍵的處理方式）在三個地方各自維護、慢慢漂移。
"""
from __future__ import annotations

# 主題累積達到這個次數才視為「已演化」的核心主題——注入 prompt 的主題
# （context_assembler.py）、拿來自動匹配 persona / 範例的主題是同一套定義。
EVOLVED_TOPIC_THRESHOLD = 3


def condition_matches(condition_json: dict, profile_row, evolved_topics: set[str]) -> bool:
    """
    比對單一條件 JSON（例：
    {"year_of_study": "Year 1", "topics_include": ["社交焦慮"]}）。
    所有出現的鍵都必須成立（AND）；空字典或含未知鍵一律視為不匹配（安全預設，
    避免設定打錯字反而意外匹配到所有人）。
    """
    if not condition_json:
        return False

    for key, value in condition_json.items():
        if key == "year_of_study":
            if profile_row is None or profile_row.year_of_study != value:
                return False
        elif key == "topics_include":
            if not evolved_topics.intersection(value or []):
                return False
        else:
            return False  # 未知條件鍵，安全預設不匹配

    return True
