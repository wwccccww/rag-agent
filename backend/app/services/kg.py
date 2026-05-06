"""
知识图谱服务（轻量级，存储于 PostgreSQL）。

数据模型：
  KGEntity   实体节点  user_id / name / entity_type / attrs / embedding
  KGRelation 关系边    subject_id --[predicate]--> object_id / confidence

核心能力：
  extract_triples   - LLM 从文本中提取 (entity, relation, entity) 三元组
  upsert_entity     - 实体查找或创建（向量去重，embedding 相近则视为同一实体）
  upsert_relation   - 关系幂等写入（同 subject+predicate+object 更新 confidence）
  graph_expand      - 从种子实体出发 N 跳展开，返回邻域上下文字符串
  search_kg         - 向量检索实体 → 图谱展开，供 search_memories 调用
"""
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import KGEntity, KGRelation, Memory
from app.services.ollama import OllamaClient

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════
# 三元组提取
# ══════════════════════════════════════════════════════

_EXTRACT_PROMPT = """\
从下面这句用户的话中提取知识三元组。
只输出纯 JSON，不要任何其他文字或 markdown。

格式：
{
  "entities": [
    {"name": "实体名称", "type": "person|project|technology|organization|concept|event|other"}
  ],
  "relations": [
    {"subject": "主语实体名", "predicate": "关系谓词（2-6字）", "object": "宾语实体名", "confidence": 0.9}
  ]
}

规则：
- 只提取用户陈述的客观事实，不要推断
- 实体名称使用原文（不翻译/不简写）
- predicate 用简短中文动词短语，如「负责」「使用」「属于」「同事」「开发」
- confidence 表示提取置信度（0.0–1.0）
- 若没有可提取的三元组，输出：{"entities": [], "relations": []}

用户输入：{text}
"""

_VALID_ENTITY_TYPES = frozenset([
    "person", "project", "technology", "organization", "concept", "event", "other"
])


def _extract_json_block(raw: str) -> dict:
    """容错解析：兼容 LLM 把 JSON 包在 ```json...``` 里"""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(l for l in lines if not l.startswith("```")).strip()
    # 有些模型会输出多段 JSON 或在前后加解释，取第一个 { 到最后一个 }
    # 并尝试去掉尾部多余内容
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start: end + 1]
    # 进一步容错：若仍解析失败，尝试抓取 "entities"/"relations" 所在的对象块
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]*?\"entities\"[\s\S]*?\"relations\"[\s\S]*?\}", text)
        if m:
            return json.loads(m.group(0))
        raise


def extract_triples(
    ollama: OllamaClient,
    user_text: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    调用 LLM 从用户文本中提取实体 + 关系三元组。

    Returns:
        (entities, relations)
        entities: [{"name": str, "type": str}]
        relations: [{"subject": str, "predicate": str, "object": str, "confidence": float}]
    """
    prompt = _EXTRACT_PROMPT.replace("{text}", user_text[:2000])
    try:
        # 使用 chat_complete_json 强制 Ollama 输出合法 JSON，避免截断/格式错误
        raw = ollama.chat_complete_json(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        logger.info("[KG] extract_triples raw response: %r", raw[:300])
        obj = _extract_json_block(raw)
        if not isinstance(obj, dict):
            logger.warning("[KG] LLM returned non-dict: %r", type(obj).__name__)
            return [], []
        entities = [
            e for e in obj.get("entities", [])
            if isinstance(e, dict) and isinstance(e.get("name"), str) and e["name"].strip()
        ]
        relations = [
            r for r in obj.get("relations", [])
            if isinstance(r, dict) and all(isinstance(r.get(k), (str, float, int)) for k in ("subject", "predicate", "object"))
        ]
        # 规范化实体类型
        for e in entities:
            e["type"] = e.get("type", "other").lower()
            if e["type"] not in _VALID_ENTITY_TYPES:
                e["type"] = "other"
        logger.info("[KG] extracted %d entities, %d relations from text", len(entities), len(relations))
        return entities, relations
    except Exception as ex:
        import traceback as _tb
        logger.warning("[KG] triple extraction failed: %r\n%s", ex, _tb.format_exc())
        return [], []


# ══════════════════════════════════════════════════════
# 实体操作
# ══════════════════════════════════════════════════════

def upsert_entity(
    db: Session,
    ollama: OllamaClient,
    user_id: str,
    name: str,
    entity_type: str = "other",
    dedup_threshold: float = 0.15,
) -> KGEntity:
    """
    查找或创建实体（按向量相似度去重）。
    embedding = embed(name + " " + entity_type)，近似相同实体更新 name/type。

    Returns:
        KGEntity 实例（已 commit）
    """
    label = f"{name} ({entity_type})"
    try:
        emb = ollama.embed(label[:500], apply_embed_budget=False)
    except Exception as ex:
        logger.warning("[KG] embed entity failed: %s", ex)
        raise

    dist_expr = KGEntity.embedding.cosine_distance(emb)
    dup = db.execute(
        select(KGEntity, dist_expr.label("dist"))
        .where(KGEntity.user_id == user_id)
        .where(dist_expr < dedup_threshold)
        .order_by(dist_expr)
        .limit(1)
    ).first()

    if dup:
        entity, dist = dup
        # 更新名字为最新表述（更精确）
        entity.name = name
        entity.entity_type = entity_type
        entity.embedding = emb
        entity.updated_at = datetime.now(timezone.utc)
        db.commit()
        logger.debug("[KG] entity merged (dist=%.3f): %s → id=%s", dist, name, entity.id)
        return entity

    entity = KGEntity(
        user_id=user_id,
        name=name,
        entity_type=entity_type,
        attrs={},
        embedding=emb,
    )
    db.add(entity)
    db.commit()
    db.refresh(entity)
    logger.debug("[KG] entity created: %s (%s) id=%s", name, entity_type, entity.id)
    return entity


def upsert_relation(
    db: Session,
    user_id: str,
    subject_id: uuid.UUID,
    predicate: str,
    object_id: uuid.UUID,
    confidence: float = 1.0,
    source_memory_id: uuid.UUID | None = None,
) -> KGRelation:
    """
    幂等写入关系：同一 (subject, predicate, object) 组合则更新 confidence，
    否则新建。
    """
    existing = db.execute(
        select(KGRelation)
        .where(KGRelation.user_id == user_id)
        .where(KGRelation.subject_id == subject_id)
        .where(KGRelation.predicate == predicate)
        .where(KGRelation.object_id == object_id)
        .limit(1)
    ).scalar_one_or_none()

    if existing:
        existing.confidence = max(existing.confidence, confidence)
        existing.updated_at = datetime.now(timezone.utc)
        if source_memory_id:
            existing.source_memory_id = source_memory_id
        db.commit()
        return existing

    rel = KGRelation(
        user_id=user_id,
        subject_id=subject_id,
        predicate=predicate,
        object_id=object_id,
        confidence=confidence,
        source_memory_id=source_memory_id,
    )
    db.add(rel)
    db.commit()
    db.refresh(rel)
    return rel


# ══════════════════════════════════════════════════════
# 图谱写入（三元组批量落库）
# ══════════════════════════════════════════════════════

def save_triples(
    db: Session,
    ollama: OllamaClient,
    user_id: str,
    entities: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    source_memory_id: uuid.UUID | None = None,
    dedup_threshold: float = 0.15,
) -> int:
    """
    将 extract_triples 的结果批量写入图谱。

    Returns:
        写入的关系条数
    """
    if not entities and not relations:
        return 0

    # 先建立实体 name → KGEntity 的映射
    name_to_entity: dict[str, KGEntity] = {}
    for e in entities:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or "").strip()
        if not name:
            continue
        try:
            ent = upsert_entity(
                db, ollama, user_id, name, str(e.get("type") or "other"), dedup_threshold
            )
            name_to_entity[name] = ent
        except Exception as ex:
            logger.warning("[KG] upsert_entity failed for %r: %s", name, ex)

    # 写入关系
    count = 0
    for r in relations:
        if not isinstance(r, dict):
            continue
        subj_name = str(r.get("subject") or "").strip()
        obj_name = str(r.get("object") or "").strip()
        predicate = str(r.get("predicate") or "").strip()[:128]
        try:
            confidence = float(r.get("confidence") or 1.0)
        except (TypeError, ValueError):
            confidence = 1.0

        if not subj_name or not obj_name or not predicate:
            continue

        # 若实体不在本批次中，尝试 upsert（可能是已有实体）
        if subj_name not in name_to_entity:
            try:
                name_to_entity[subj_name] = upsert_entity(db, ollama, user_id, subj_name, "other", dedup_threshold)
            except Exception:
                continue
        if obj_name not in name_to_entity:
            try:
                name_to_entity[obj_name] = upsert_entity(db, ollama, user_id, obj_name, "other", dedup_threshold)
            except Exception:
                continue

        subj_ent = name_to_entity.get(subj_name)
        obj_ent = name_to_entity.get(obj_name)
        if not subj_ent or not obj_ent:
            logger.warning("[KG] skip relation %r→%r: entity missing after upsert", subj_name, obj_name)
            continue

        try:
            upsert_relation(
                db, user_id,
                subject_id=subj_ent.id,
                predicate=predicate,
                object_id=obj_ent.id,
                confidence=confidence,
                source_memory_id=source_memory_id,
            )
            count += 1
        except Exception as ex:
            logger.warning("[KG] upsert_relation failed: %s", ex)

    logger.info("[KG] saved %d relations for user %s", count, user_id)
    return count


# ══════════════════════════════════════════════════════
# 图谱展开（N 跳邻域查询）
# ══════════════════════════════════════════════════════

def graph_expand(
    db: Session,
    user_id: str,
    seed_entity_ids: list[uuid.UUID],
    hops: int = 2,
) -> list[str]:
    """
    从种子实体出发，展开最多 hops 跳的邻域关系，返回可读文本列表。

    每条记录格式："{主语} --[{谓词}]--> {宾语}"

    Returns:
        list[str]，每条为一个关系三元组的自然语言描述
    """
    if not seed_entity_ids:
        return []

    visited_ids: set[uuid.UUID] = set(seed_entity_ids)
    current_ids = list(seed_entity_ids)
    lines: list[str] = []

    for _ in range(hops):
        if not current_ids:
            break

        # 以 current_ids 为主语或宾语的所有关系
        stmt = (
            select(KGRelation, KGEntity)
            .join(KGEntity, KGEntity.id == KGRelation.subject_id)
            .where(KGRelation.user_id == user_id)
            .where(
                KGRelation.subject_id.in_(current_ids) | KGRelation.object_id.in_(current_ids)
            )
            .order_by(KGRelation.confidence.desc())
            .limit(50)  # 单跳最多展开 50 条，防止爆炸
        )
        rows = db.execute(stmt).all()

        next_ids: list[uuid.UUID] = []
        for rel, subj_entity in rows:
            # 获取宾语实体
            obj_entity = db.get(KGEntity, rel.object_id)
            if not obj_entity:
                continue
            line = f"{subj_entity.name}({subj_entity.entity_type}) --[{rel.predicate}]--> {obj_entity.name}({obj_entity.entity_type})"
            if line not in lines:
                lines.append(line)
            # 收集下一跳种子
            for eid in (rel.subject_id, rel.object_id):
                if eid not in visited_ids:
                    visited_ids.add(eid)
                    next_ids.append(eid)

        current_ids = next_ids

    return lines


# ══════════════════════════════════════════════════════
# 向量检索实体 + 图谱展开（供 search_memories 调用）
# ══════════════════════════════════════════════════════

def search_kg(
    db: Session,
    ollama: OllamaClient,
    user_id: str,
    query: str,
    top_k_entities: int = 5,
    hops: int = 2,
) -> list[str]:
    """
    向量检索最相关的实体，再从这些实体展开 hops 跳的图谱邻域。

    Returns:
        list[str]，每条为一个关系三元组描述，供注入 LLM 上下文。
    """
    try:
        qemb = ollama.embed(query[:500], apply_embed_budget=False)
    except Exception as ex:
        logger.warning("[KG] search_kg embed failed: %s", ex)
        return []

    dist_expr = KGEntity.embedding.cosine_distance(qemb)
    rows = db.execute(
        select(KGEntity, dist_expr.label("dist"))
        .where(KGEntity.user_id == user_id)
        .where(dist_expr < 0.5)   # 距离阈值，避免完全不相关的实体
        .order_by(dist_expr)
        .limit(top_k_entities)
    ).all()

    if not rows:
        return []

    seed_ids = [ent.id for ent, _ in rows]
    logger.info("[KG] found %d seed entities for query %r", len(seed_ids), query[:40])
    return graph_expand(db, user_id, seed_ids, hops=hops)
