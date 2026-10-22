"""
共用的「條件 JSON 比對」邏輯（Phase 3 從 persona_resolver.py 抽出）

persona_resolver.py（persona_match_conditions）、example_selector.py
（response_examples.applicable_conditions_json），以及未來 Phase 4 的
rule_engine.py（rules.conditions_json）都從這裡 import，避免同一套比對規則
（主題門檻、未知條件鍵的處理方式）在三個地方各自維護、慢慢漂移。
"""
from __future__ import annotations

from app.core.clinical_topics import STANDARD_CLINICAL_TOPICS

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


RISK_LEVELS = ("none", "low", "medium", "high")


def validate_condition_json(condition_json: dict, mode: str = "default") -> dict:
    """
    建立/修改條件時的格式檢查（Admin API 用，Phase 4 rules CRUD 也可沿用）。
    condition_matches() 對空條件、未知鍵、型別錯誤都是「靜默不匹配」——執行期這是對的
    安全預設，但在建立時放行，治療師會拿到一筆永遠不會生效、又看不出原因的設定。
    例：{"topics_include": "Relationship"}（字串不是 list）會被逐字元比對、永遠不命中；
    打錯字的 "topics_includ" 會被當未知鍵。這裡提早丟 ValueError 讓 API 回 422。

    mode="rule"（Phase 4 rules.conditions_json）額外接受 risk_level 與
    min_topic_mentions，且 topics_include 只接受 STANDARD_CLINICAL_TOPICS——Profile
    分類只會寫入這份英文清單，清單外的主題（例如「社交焦慮」）永遠不會命中。
    persona / example 維持原本格式（mode="default"），行為不變。
    """
    if mode == "rule":
        return _validate_rule_conditions(condition_json)

    if not condition_json:
        raise ValueError("條件不可為空（空條件永遠不會命中）")

    for key, value in condition_json.items():
        if key == "year_of_study":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("year_of_study 必須是非空字串")
        elif key == "topics_include":
            if (
                not isinstance(value, list)
                or not value
                or not all(isinstance(t, str) and t.strip() for t in value)
            ):
                raise ValueError("topics_include 必須是非空的字串陣列")
        else:
            raise ValueError(f"不支援的條件鍵：{key}（目前只支援 year_of_study、topics_include）")

    return condition_json


def _validate_rule_conditions(condition_json: dict) -> dict:
    if not condition_json:
        raise ValueError("條件不可為空（空條件永遠不會命中）")

    for key, value in condition_json.items():
        if key == "year_of_study":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("year_of_study 必須是非空字串")
        elif key == "topics_include":
            if not isinstance(value, list) or not value:
                raise ValueError("topics_include 必須是非空的字串陣列")
            unknown = [t for t in value if t not in STANDARD_CLINICAL_TOPICS]
            if unknown:
                raise ValueError(
                    f"topics_include 只接受標準主題 {STANDARD_CLINICAL_TOPICS}，不認得：{unknown}"
                )
        elif key == "risk_level":
            if value not in RISK_LEVELS:
                raise ValueError(f"risk_level 必須是 {RISK_LEVELS} 之一")
        elif key == "min_topic_mentions":
            # bool 是 int 的子類別，要排除 True/False
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("min_topic_mentions 必須是正整數")
            if "topics_include" not in condition_json:
                raise ValueError("min_topic_mentions 必須搭配 topics_include")
        else:
            raise ValueError(
                f"不支援的條件鍵：{key}（規則支援 year_of_study、topics_include、risk_level、min_topic_mentions）"
            )

    return condition_json


def rule_condition_matches(condition_json: dict, profile_row, topic_counts: dict[str, int]) -> bool:
    """
    比對 Phase 4 規則條件。跟 condition_matches() 同樣是 AND 邏輯、空條件或未知鍵
    一律不匹配，差別在主題門檻可由 min_topic_mentions 自訂（省略時沿用
    EVOLVED_TOPIC_THRESHOLD），所以需要逐主題的次數，而不是「已演化主題」集合。
    topics_include 中任一主題自身達門檻即成立，不同主題的次數不合計。
    """
    if not condition_json:
        return False

    threshold = condition_json.get("min_topic_mentions", EVOLVED_TOPIC_THRESHOLD)
    for key, value in condition_json.items():
        if key == "year_of_study":
            if profile_row is None or profile_row.year_of_study != value:
                return False
        elif key == "risk_level":
            if profile_row is None or profile_row.risk_level != value:
                return False
        elif key == "topics_include":
            if not any(topic_counts.get(t, 0) >= threshold for t in value or []):
                return False
        elif key == "min_topic_mentions":
            if "topics_include" not in condition_json:
                return False
        else:
            return False  # 未知條件鍵，安全預設不匹配

    return True
