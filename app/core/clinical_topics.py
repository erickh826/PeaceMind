"""
標準臨床關注主題清單（Phase 2）
單一共用來源，供 profile_service.py 的分類 prompt、Phase 2.8 的 persona 自動匹配、
以及 Phase 4 Rule Engine 共用引用 —— 避免同一份清單在多處各自漂移。
"""

STANDARD_CLINICAL_TOPICS = [
    "Relationship",
    "Academic Stress",
    "Family Conflict",
    "Self-Esteem",
    "Career Uncertainty",
    "Anxiety",
    "Others",
]
