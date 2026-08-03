import json
import hashlib
import litellm

from string import Template

from collections import defaultdict

from typing import Optional, Any

from app.models.auth import TokenUserInfo
from app.models.graphrag import (
    Entity,
    Relationship,
    ExtractionResult,
    GraphSearchResult,
)
from app.utils.memgraph import MemgraphManager, get_memgraph_manager
from app.utils.vector_store import VectorStoreManager, get_vector_store_manager
from app.utils.litellm import get_litellm_model_name, get_litellm_params
from app.utils.logger import get_logger
import app.models.db_item as db_items


logger = get_logger("graphrag", log_dir="logs")


# 엔티티/관계 추출 프롬프트
ENTITY_EXTRACTION_PROMPT = Template("""You are a knowledge graph extraction expert. Your task is to build a comprehensive knowledge graph from the given text.

## EXTRACTION RULES

### 1. Entity Extraction
Extract ALL significant entities from the text:
- **PERSON**: People, characters, individuals (e.g., 홍길동, John Smith)
- **ORGANIZATION**: Companies, institutions, teams, departments (e.g., 삼성전자, Microsoft)
- **LOCATION**: Places, addresses, regions (e.g., 서울, New York)
- **TECHNOLOGY**: Technologies, tools, frameworks, languages (e.g., Python, GraphRAG, AI)
- **PRODUCT**: Products, services, systems (e.g., iPhone, Windows)
- **CONCEPT**: Abstract concepts, methodologies, theories (e.g., 머신러닝, Agile)
- **EVENT**: Events, meetings, milestones (e.g., 2024년 컨퍼런스)
- **DOCUMENT**: Documents, reports, papers (e.g., 사업계획서)
- **OTHER**: Any other important entity

For each entity provide:
- **name**: The EXACT name as it appears in the text (preserve original spelling/language)
- **type**: One of the types above
- **description**: A brief description based on context

### 2. Relationship Extraction (CRITICAL)
Extract ALL relationships between entities. This is the most important part!

**IMPORTANT RULES:**
- The "source" and "target" MUST be EXACTLY the same as one of the entity names you extracted above
- Look for both explicit and implicit relationships
- Each pair of related entities should have at least one relationship
- Be thorough - extract as many meaningful relationships as possible

Common relationship types:
- WORKS_FOR, EMPLOYED_BY (employment)
- CREATED_BY, DEVELOPED_BY, AUTHORED_BY (creation)
- USES, UTILIZES, IMPLEMENTS (usage)
- BELONGS_TO, PART_OF, MEMBER_OF (membership)
- LOCATED_IN, BASED_IN (location)
- RELATED_TO, ASSOCIATED_WITH (general relation)
- DEPENDS_ON, REQUIRES (dependency)
- PRODUCES, OUTPUTS (production)
- MANAGES, LEADS, SUPERVISES (management)
- COLLABORATES_WITH, PARTNERS_WITH (collaboration)

For each relationship provide:
- **source**: EXACT entity name from your entity list
- **target**: EXACT entity name from your entity list  
- **relation_type**: Type of relationship (use UPPERCASE with underscores)
- **description**: Brief description of the relationship

## OUTPUT FORMAT
Respond ONLY with valid JSON:
```json
{
    "entities": [
        {"name": "엔티티명", "type": "TYPE", "description": "설명"}
    ],
    "relationships": [
        {"source": "출발엔티티", "target": "도착엔티티", "relation_type": "RELATION_TYPE", "description": "관계 설명"}
    ]
}
```

## CRITICAL REQUIREMENTS
1. Extract at least 3-5 relationships if possible
2. The source and target in relationships MUST exactly match entity names
3. Use the SAME LANGUAGE as the input text for names and descriptions
4. DO NOT translate - preserve original language exactly
5. Be thorough - missing relationships means incomplete knowledge graph

## TEXT TO ANALYZE
```txt
${text}
```""")

# 커뮤니티 요약 프롬프트
COMMUNITY_SUMMARY_PROMPT = Template("""Write a concise summary of the group (community) to which the following entities belong.
Explain what this group represents and which topics or domains it is related to.

Entity list:
```txt
${members_text}
```

IMPORTANT: Write the summary in the same language as the input entities.  
Do not translate; preserve the original language exactly.

Summary (2–3 sentences, same language as input entities):
""")


class GraphRAGService:
    """GraphRAG 서비스"""
    
    def __init__(self, memgraph_manager: MemgraphManager, vector_store: Optional[VectorStoreManager] = None):
        self.memgraph = memgraph_manager
        self.vector_store = vector_store
    
    async def extract_entities_and_relationships(self, text: str, model: db_items.Model, user_info: TokenUserInfo) -> ExtractionResult:
        """텍스트에서 엔티티와 관계를 추출합니다.
        
        Args:
            text (str): 분석할 텍스트
            model (db_items.Model): 사용할 LLM 모델
            user_info (TokenUserInfo): 사용자 정보
        
        Returns:
            ExtractionResult: 추출된 엔티티와 관계
        """
        
        prompt = ENTITY_EXTRACTION_PROMPT.substitute(text=text)
        
        litellm_model = get_litellm_model_name(model.provider, model.name, model.model_id)
        litellm_params = get_litellm_params(model, user_info)
        
        try:
            response = await litellm.acompletion(
                model=litellm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                response_format={"type": "json_object"},
                **litellm_params
            )
            
            content = response.choices[0].message.content
            
            # JSON 파싱
            json_start = content.find("{")
            json_end = content.rfind("}") + 1
            if json_start != -1 and json_end > json_start:
                json_str = content[json_start:json_end]
                data = json.loads(json_str)
                
                entities = [Entity(**e) for e in data.get("entities", [])]
                relationships = [Relationship(**r) for r in data.get("relationships", [])]
                
                return ExtractionResult(entities=entities, relationships=relationships)
            
            return ExtractionResult(entities=[], relationships=[])
        except Exception:
            logger.exception("엔티티/관계 추출 중 오류가 발생했습니다.")
            return ExtractionResult(entities=[], relationships=[])
    
    def _generate_entity_id(self, name: str, collection_name: str) -> str:
        """엔티티 ID를 생성합니다.
        
        Args:
            name (str): 엔티티 이름
            collection_name (str): 컬렉션 이름
        
        Returns:
            str: 생성된 엔티티 ID
        """
        
        # 이름 정규화: 소문자 변환, 양쪽 공백 제거, 연속 공백을 단일 공백으로
        normalized_name = " ".join(name.lower().split())
        key = f"{collection_name}:{normalized_name}"
        return hashlib.md5(key.encode()).hexdigest()[:16]
    
    def _normalize_entity_name(self, name: str) -> str:
        """엔티티 이름을 정규화합니다.
        
        Args:
            name (str): 엔티티 이름
        
        Returns:
            str: 정규화된 이름
        """
        
        # 양쪽 공백 제거, 연속 공백을 단일 공백으로
        return " ".join(name.strip().split())
    
    def store_entities(self, collection_name: str, document_id: int, chunk_index: int, entities: list[Entity]) -> int:
        """엔티티를 그래프에 저장합니다.
        
        Args:
            collection_name (str): 컬렉션 이름
            document_id (int): 문서 ID
            chunk_index (int): 청크 인덱스
            entities (list[Entity]): 저장할 엔티티 목록
        
        Returns:
            int: 저장된 엔티티 수
        """
        
        count = 0
        for entity in entities:
            # 엔티티 이름 정규화
            normalized_name = self._normalize_entity_name(entity.name)
            entity_id = self._generate_entity_id(normalized_name, collection_name)
            
            query = """
            MERGE (e:Entity {id: $id})
            ON CREATE SET
                e.name = $name,
                e.name_lower = $name_lower,
                e.type = $type,
                e.description = $description,
                e.collection = $collection,
                e.document_ids = [$document_id],
                e.chunk_indices = [$chunk_index],
                e.mention_count = 1
            ON MATCH SET
                e.description = CASE WHEN e.description IS NULL OR size(e.description) < size($description) 
                                     THEN $description ELSE e.description END,
                e.document_ids = CASE WHEN NOT $document_id IN e.document_ids 
                                      THEN e.document_ids + [$document_id] ELSE e.document_ids END,
                e.chunk_indices = e.chunk_indices + [$chunk_index],
                e.mention_count = e.mention_count + 1
            """
            
            try:
                self.memgraph.execute_write(query, {
                    "id": entity_id,
                    "name": normalized_name,
                    "name_lower": normalized_name.lower(),
                    "type": entity.type,
                    "description": entity.description or "",
                    "collection": collection_name,
                    "document_id": document_id,
                    "chunk_index": chunk_index
                })
                count += 1
            except Exception:
                logger.exception(f"엔티티 저장 중 오류가 발생했습니다. (entity={entity.name})")
        
        return count
    
    def _find_entity_id(self, name: str, collection_name: str) -> Optional[str]:
        """엔티티 이름으로 ID를 찾습니다. 정확한 매칭 또는 fuzzy 매칭을 수행합니다.
        
        Args:
            name (str): 엔티티 이름
            collection_name (str): 컬렉션 이름
        
        Returns:
            Optional[str]: 엔티티 ID 또는 None
        """
        
        normalized_name = self._normalize_entity_name(name)
        
        # 정확한 ID 매칭 시도
        exact_id = self._generate_entity_id(normalized_name, collection_name)
        result = self.memgraph.execute_read("""
            MATCH (e:Entity {id: $id, collection: $collection})
            RETURN e.id as id
        """, {"id": exact_id, "collection": collection_name})
        
        if result:
            return result[0]["id"]
        
        # 이름 기반 fuzzy 매칭 시도 (소문자 비교)
        name_lower = normalized_name.lower()
        result = self.memgraph.execute_read("""
            MATCH (e:Entity {collection: $collection})
            WHERE e.name_lower = $name_lower
               OR e.name_lower CONTAINS $name_lower
               OR $name_lower CONTAINS e.name_lower
            RETURN e.id as id, e.name as name
            ORDER BY size(e.name) DESC
            LIMIT 1
        """, {"collection": collection_name, "name_lower": name_lower})
        
        if result:
            return result[0]["id"]
        
        return None
    
    def store_relationships(self, collection_name: str, document_id: int, relationships: list[Relationship]) -> int:
        """관계를 그래프에 저장합니다.
        
        Args:
            collection_name (str): 컬렉션 이름
            document_id (int): 문서 ID
            relationships (list[Relationship]): 저장할 관계 목록
        
        Returns:
            int: 저장된 관계 수
        """
        
        count = 0
        
        for rel in relationships:
            # 엔티티 ID 찾기
            source_id = self._find_entity_id(rel.source, collection_name)
            target_id = self._find_entity_id(rel.target, collection_name)
            
            if not source_id:
                logger.debug(f"엔티티를 찾을 수 없어 관계를 건너뜁니다. (source={rel.source}, target={rel.target})")
                continue
            
            if not target_id:
                logger.debug(f"엔티티를 찾을 수 없어 관계를 건너뜁니다. (source={rel.source}, target={rel.target})")
                continue
            
            try:
                # 관계 저장
                query = """
                MATCH (s:Entity {id: $source_id}), (t:Entity {id: $target_id})
                MERGE (s)-[r:RELATES_TO {type: $relation_type}]->(t)
                ON CREATE SET
                    r.description = $description,
                    r.document_ids = [$document_id],
                    r.weight = 1
                ON MATCH SET
                    r.document_ids = CASE WHEN NOT $document_id IN r.document_ids 
                                          THEN r.document_ids + [$document_id] ELSE r.document_ids END,
                    r.weight = r.weight + 1
                """
                
                self.memgraph.execute_write(query, {
                    "source_id": source_id,
                    "target_id": target_id,
                    "relation_type": rel.relation_type.upper().replace(" ", "_"),
                    "description": rel.description or "",
                    "document_id": document_id
                })
                count += 1
            except Exception:
                logger.exception(f"관계 저장 중 오류가 발생했습니다. (source={rel.source}, target={rel.target})")
        
        return count
    
    def store_facility_entities(self, collection_name: str, facilities: list[dict[str, Any]]) -> int:
        """구조화된 시설(Facility) 데이터를 LLM 엔티티 추출 없이 그래프에 바로 upsert합니다.

        정형 데이터(이름/유형/주소/연락처)이므로 LLM으로 재해석할 필요가 없어,
        엔티티 ID를 facility_id 기준으로 고정해 재동기화 시에도 같은 노드를 갱신합니다.

        Args:
            collection_name (str): 컬렉션 이름
            facilities (list[dict]): facility_id/name/facility_type/address/phone을 담은 딕셔너리 목록

        Returns:
            int: 저장(upsert)된 시설 수
        """

        count = 0
        for facility in facilities:
            entity_id = f"facility:{facility['facility_id']}"
            description = " · ".join(filter(None, [
                facility.get("facility_type"),
                facility.get("address"),
                facility.get("phone"),
            ]))

            query = """
            MERGE (e:Entity {id: $id})
            SET
                e.name = $name,
                e.name_lower = $name_lower,
                e.type = 'FACILITY',
                e.description = $description,
                e.collection = $collection,
                e.facility_id = $facility_id,
                e.facility_type = $facility_type,
                e.address = $address,
                e.phone = $phone,
                e.mention_count = coalesce(e.mention_count, 1)
            """

            try:
                self.memgraph.execute_write(query, {
                    "id": entity_id,
                    "name": facility["name"],
                    "name_lower": facility["name"].lower(),
                    "description": description,
                    "collection": collection_name,
                    "facility_id": facility["facility_id"],
                    "facility_type": facility.get("facility_type") or "",
                    "address": facility.get("address") or "",
                    "phone": facility.get("phone") or "",
                })
                count += 1
            except Exception:
                logger.exception(f"시설 저장 중 오류가 발생했습니다. (facility_id={facility.get('facility_id')})")

        return count

    def search_by_keywords(self, collection_name: str, keywords: list[str], top_k: int = 10) -> GraphSearchResult:
        """LLM 엔티티 추출 없이 키워드 매칭만으로 그래프를 검색합니다.

        구조화 데이터(예: 시설)처럼 쿼리에서 엔티티를 다시 추출할 필요가 없는 경우를 위한
        저지연/무비용 검색 경로입니다. `search()`의 키워드 매칭 폴백 로직과 동일한 방식입니다.

        Args:
            collection_name (str): 컬렉션 이름
            keywords (list[str]): 매칭할 키워드 목록
            top_k (int): 반환할 결과 수

        Returns:
            GraphSearchResult: 검색 결과 (documents는 항상 빈 리스트)
        """

        query_words = [w.lower() for w in keywords if w]
        if not query_words:
            return GraphSearchResult(entities=[], relationships=[], communities=[], documents=[])

        entities = self.memgraph.execute_read("""
            MATCH (e:Entity {collection: $collection})
            WHERE any(word in $query_words WHERE toLower(e.name) CONTAINS word)
               OR any(word in $query_words WHERE toLower(e.description) CONTAINS word)
            RETURN e.id as id, e.name as name, e.type as type,
                   e.description as description, e.mention_count as mention_count,
                   e.community_id as community_id
            ORDER BY e.mention_count DESC
            LIMIT $top_k
        """, {
            "collection": collection_name,
            "query_words": query_words,
            "top_k": top_k
        })

        entity_ids = [e["id"] for e in entities]
        relationships = []
        if entity_ids:
            relationships = self.memgraph.execute_read("""
                MATCH (s:Entity)-[r:RELATES_TO]->(t:Entity)
                WHERE s.id IN $entity_ids OR t.id IN $entity_ids
                RETURN s.id as source_id, s.name as source_name, s.type as source_type,
                       t.id as target_id, t.name as target_name, t.type as target_type,
                       r.type as relation_type, r.description as description
                LIMIT $limit
            """, {"entity_ids": entity_ids, "limit": top_k * 2})

        return GraphSearchResult(entities=entities, relationships=relationships, communities=[], documents=[])

    def detect_communities(self, collection_name: str) -> int:
        """커뮤니티를 탐지하고 레이블을 할당합니다.
        
        Memgraph의 Label Propagation 알고리즘 사용
        
        Args:
            collection_name (str): 컬렉션 이름
        
        Returns:
            int: 탐지된 커뮤니티 수
        """
        
        # 커뮤니티 탐지 쿼리 (Memgraph MAGE 라이브러리 사용)
        try:
            # 기존 커뮤니티 레이블 제거
            self.memgraph.execute_write("""
                MATCH (e:Entity {collection: $collection})
                REMOVE e.community_id
            """, {"collection": collection_name})
            
            # 연결 컴포넌트 기반 커뮤니티 할당
            result = self.memgraph.execute_read("""
                MATCH (e:Entity {collection: $collection})
                WITH collect(e) as nodes
                UNWIND nodes as node
                OPTIONAL MATCH (node)-[:RELATES_TO]-(neighbor:Entity {collection: $collection})
                WITH node, collect(DISTINCT neighbor) as neighbors
                RETURN node.id as node_id, [n in neighbors | n.id] as neighbor_ids
            """, {"collection": collection_name})
            
            # 간단한 연결 컴포넌트 기반 커뮤니티 할당
            if result:
                # 그래프 구축
                graph = defaultdict(set)
                all_nodes = set()
                
                for row in result:
                    node_id = row["node_id"]
                    all_nodes.add(node_id)
                    for neighbor_id in row["neighbor_ids"]:
                        graph[node_id].add(neighbor_id)
                        graph[neighbor_id].add(node_id)
                
                # 연결 컴포넌트 찾기 (BFS)
                visited = set()
                community_id = 0
                
                for node in all_nodes:
                    if node in visited:
                        continue
                    
                    # BFS로 컴포넌트 탐색
                    queue = [node]
                    component = []
                    
                    while queue:
                        current = queue.pop(0)
                        if current in visited:
                            continue
                        visited.add(current)
                        component.append(current)
                        
                        for neighbor in graph[current]:
                            if neighbor not in visited:
                                queue.append(neighbor)
                    
                    # 커뮤니티 ID 할당
                    for node_id in component:
                        self.memgraph.execute_write("""
                            MATCH (e:Entity {id: $node_id})
                            SET e.community_id = $community_id
                        """, {"node_id": node_id, "community_id": community_id})
                    
                    community_id += 1
                
                logger.debug(f"{community_id}개의 커뮤니티가 탐지되었습니다. (collection={collection_name})")
                return community_id
            
            return 0
        except Exception:
            logger.exception("커뮤니티 탐지 중 오류가 발생했습니다.")
            return 0
    
    async def generate_community_summaries(self, collection_name: str, model: db_items.Model, user_info: TokenUserInfo) -> int:
        """각 커뮤니티에 대한 요약을 생성합니다.
        
        Args:
            collection_name (str): 컬렉션 이름
            model (db_items.Model): 사용할 LLM 모델
            user_info (TokenUserInfo): 사용자 정보
        
        Returns:
            int: 생성된 요약 수
        """
        
        # 커뮤니티별 엔티티 조회
        communities = self.memgraph.execute_read("""
            MATCH (e:Entity {collection: $collection})
            WHERE e.community_id IS NOT NULL
            WITH e.community_id as community_id, collect(e) as entities
            RETURN community_id, 
                   [entity in entities | {name: entity.name, type: entity.type, description: entity.description}] as members
            ORDER BY community_id
        """, {"collection": collection_name})
        
        count = 0
        litellm_model = get_litellm_model_name(model.provider, model.name, model.model_id)
        litellm_params = get_litellm_params(model, user_info)
        
        for community in communities:
            community_id = community["community_id"]
            members = community["members"]
            
            if not members:
                continue
            
            # 요약 생성 프롬프트
            members_text = "\n".join([
                f"- {m['name']} ({m['type']}): {m.get('description', 'N/A')}"
                for m in members
            ])
            
            prompt = COMMUNITY_SUMMARY_PROMPT.substitute(members_text=members_text)
            
            try:
                response = await litellm.acompletion(
                    model=litellm_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=200,
                    **litellm_params
                )
                
                summary = response.choices[0].message.content.strip()
                
                # 커뮤니티 요약 저장
                self.memgraph.execute_write("""
                    MERGE (c:Community {id: $community_id, collection: $collection})
                    SET c.summary = $summary, c.member_count = $member_count
                """, {
                    "community_id": community_id,
                    "collection": collection_name,
                    "summary": summary,
                    "member_count": len(members)
                })
                
                count += 1
            except Exception:
                logger.exception(f"커뮤니티 요약 생성 중 오류가 발생했습니다. (community_id={community_id})")
        
        logger.debug(f"{count}개의 커뮤니티 요약이 생성되었습니다. (collection={collection_name})")
        return count
    
    async def _get_original_chunks(self, collection_name: str, entity_ids: list[str]) -> list[dict[str, Any]]:
        """엔티티와 관련된 원본 청크를 벡터 저장소에서 조회합니다.
        
        Args:
            collection_name (str): 컬렉션 이름
            entity_ids (list[str]): 엔티티 ID 리스트
        
        Returns:
            list[dict]: 원본 청크 리스트
        """
        
        if not self.vector_store or not entity_ids:
            return []
        
        try:
            # 엔티티의 document_id와 chunk_index 조회
            entity_chunks = self.memgraph.execute_read("""
                MATCH (e:Entity {collection: $collection})
                WHERE e.id IN $entity_ids
                RETURN e.id as entity_id, e.document_ids as document_ids, e.chunk_indices as chunk_indices
            """, {"collection": collection_name, "entity_ids": entity_ids})
            
            # document_id와 chunk_index 쌍 수집
            chunk_keys = set()
            for entity in entity_chunks:
                doc_ids = entity.get("document_ids", [])
                chunk_indices = entity.get("chunk_indices", [])
                for doc_id, chunk_idx in zip(doc_ids, chunk_indices):
                    chunk_keys.add((doc_id, chunk_idx))
            
            # 벡터 저장소에서 원본 청크 조회
            original_chunks = []
            for doc_id, chunk_idx in chunk_keys:
                chunks = await self.vector_store.get_points_by_document_id_async(collection_name, doc_id)
                for chunk in chunks:
                    if chunk.get("chunk_index") == chunk_idx:
                        original_chunks.append({
                            "document_id": doc_id,
                            "chunk_index": chunk_idx,
                            "content": chunk.get("content", ""),
                            "file_name": chunk.get("file_name", "")
                        })
            
            return original_chunks[:10]  # 최대 10개로 제한
        except Exception:
            logger.exception("원본 청크 조회 중 오류가 발생했습니다.")
            return []
    
    async def search(self, collection_name: str, query: str, model: db_items.Model, user_info: TokenUserInfo, top_k: int = 10) -> GraphSearchResult:
        """지식 그래프에서 검색을 수행합니다.
        
        Args:
            collection_name (str): 컬렉션 이름
            query (str): 검색 쿼리
            model (db_items.Model): 사용할 LLM 모델
            user_info (TokenUserInfo): 사용자 정보
            top_k (int): 반환할 결과 수
        
        Returns:
            GraphSearchResult: 검색 결과
        """
        
        # 쿼리에서 엔티티 추출
        extraction = await self.extract_entities_and_relationships(query, model, user_info)
        query_entities = [e.name.lower() for e in extraction.entities]
        
        # 엔티티 이름으로 검색 (정확 매칭 + 부분 매칭)
        if query_entities:
            entities = self.memgraph.execute_read("""
                MATCH (e:Entity {collection: $collection})
                WHERE toLower(e.name) IN $query_entities
                   OR any(qe in $query_entities WHERE toLower(e.name) CONTAINS qe)
                RETURN e.id as id, e.name as name, e.type as type, 
                       e.description as description, e.mention_count as mention_count,
                       e.community_id as community_id
                ORDER BY e.mention_count DESC
                LIMIT $top_k
            """, {
                "collection": collection_name,
                "query_entities": query_entities,
                "top_k": top_k
            })
        else:
            # 쿼리 단어로 검색
            query_words = [w.lower() for w in query.split() if len(w) > 2]
            entities = self.memgraph.execute_read("""
                MATCH (e:Entity {collection: $collection})
                WHERE any(word in $query_words WHERE toLower(e.name) CONTAINS word)
                   OR any(word in $query_words WHERE toLower(e.description) CONTAINS word)
                RETURN e.id as id, e.name as name, e.type as type,
                       e.description as description, e.mention_count as mention_count,
                       e.community_id as community_id
                ORDER BY e.mention_count DESC
                LIMIT $top_k
            """, {
                "collection": collection_name,
                "query_words": query_words,
                "top_k": top_k
            })
        
        # 관련 관계 조회
        entity_ids = [e["id"] for e in entities]
        relationships = []
        if entity_ids:
            relationships = self.memgraph.execute_read("""
                MATCH (s:Entity)-[r:RELATES_TO]->(t:Entity)
                WHERE s.id IN $entity_ids OR t.id IN $entity_ids
                RETURN s.id as source_id, s.name as source_name, s.type as source_type,
                       t.id as target_id, t.name as target_name, t.type as target_type,
                       r.type as relation_type, r.description as description
                LIMIT $limit
            """, {"entity_ids": entity_ids, "limit": top_k * 2})
        
        # 관련 커뮤니티 조회
        community_ids = list(set([e.get("community_id") for e in entities if e.get("community_id") is not None]))
        communities = []
        if community_ids:
            communities = self.memgraph.execute_read("""
                MATCH (c:Community {collection: $collection})
                WHERE c.id IN $community_ids
                RETURN c.id as id, c.summary as summary, c.member_count as member_count
            """, {"collection": collection_name, "community_ids": community_ids})
        
        # 관련 문서 청크 조회
        documents = await self._get_original_chunks(collection_name, entity_ids)
        
        return GraphSearchResult(
            entities=entities,
            relationships=relationships,
            communities=communities,
            documents=documents
        )
    
    def delete_collection_graph(self, collection_name: str):
        """컬렉션의 그래프 데이터를 삭제합니다.
        
        Args:
            collection_name (str): 컬렉션 이름
        """
        
        try:
            # 관계 삭제
            self.memgraph.execute_write("""
                MATCH (e:Entity {collection: $collection})-[r]-()
                DELETE r
            """, {"collection": collection_name})
            
            # 엔티티 삭제
            self.memgraph.execute_write("""
                MATCH (e:Entity {collection: $collection})
                DELETE e
            """, {"collection": collection_name})
            
            # 커뮤니티 삭제
            self.memgraph.execute_write("""
                MATCH (c:Community {collection: $collection})
                DELETE c
            """, {"collection": collection_name})
            
            logger.debug(f"그래프 데이터가 삭제되었습니다. (collection={collection_name})")
        except Exception:
            logger.exception("그래프 데이터 삭제 중 오류가 발생했습니다.")
    
    def get_collection_stats(self, collection_name: str) -> dict[str, Any]:
        """컬렉션의 그래프 통계를 조회합니다.
        
        Args:
            collection_name (str): 컬렉션 이름
        
        Returns:
            dict: 통계 정보
        """
        
        try:
            entity_count = self.memgraph.execute_read("""
                MATCH (e:Entity {collection: $collection})
                RETURN count(e) as count
            """, {"collection": collection_name})
            
            relationship_count = self.memgraph.execute_read("""
                MATCH (s:Entity {collection: $collection})-[r:RELATES_TO]->(t:Entity)
                RETURN count(r) as count
            """, {"collection": collection_name})
            
            community_count = self.memgraph.execute_read("""
                MATCH (c:Community {collection: $collection})
                RETURN count(c) as count
            """, {"collection": collection_name})
            
            return {
                "entity_count": entity_count[0]["count"] if entity_count else 0,
                "relationship_count": relationship_count[0]["count"] if relationship_count else 0,
                "community_count": community_count[0]["count"] if community_count else 0
            }
        except Exception:
            logger.exception("통계 조회 중 오류가 발생했습니다.")
            return {"entity_count": 0, "relationship_count": 0, "community_count": 0}

    def get_visualization_data(
        self, 
        collection_name: str, 
        limit: Optional[int] = None,
        min_mention_count: int = 1,
        entity_types: Optional[list[str]] = None
    ) -> dict[str, Any]:
        """그래프 시각화를 위한 노드와 엣지 데이터를 반환합니다.
        
        Args:
            collection_name (str): 컬렉션 이름
            limit (Optional[int]): 반환할 최대 노드 수 (None이면 전체)
            min_mention_count (int): 최소 언급 횟수 (기본값: 1)
            entity_types (Optional[list[str]]): 필터링할 엔티티 타입 목록
        
        Returns:
            dict: 시각화 데이터 (nodes, edges, communities, stats)
        """
        
        try:
            # 엔티티 조회
            where_clauses = [f"e.collection = $collection", f"e.mention_count >= $min_mention_count"]
            
            if entity_types:
                where_clauses.append("e.type IN $entity_types")
            
            where_clause = " AND ".join(where_clauses)
            
            query = f"""
                MATCH (e:Entity)
                WHERE {where_clause}
                RETURN e.id as id, e.name as name, e.type as type,
                       e.description as description, e.mention_count as mention_count,
                       e.community_id as community_id, e.document_ids as document_ids
                ORDER BY e.mention_count DESC
            """
            
            if limit:
                query += f" LIMIT {limit}"
            
            params = {
                "collection": collection_name,
                "min_mention_count": min_mention_count
            }
            if entity_types:
                params["entity_types"] = entity_types
            
            entities = self.memgraph.execute_read(query, params)
            
            # 노드 데이터 생성
            nodes = []
            entity_ids = []
            for e in entities:
                nodes.append({
                    "id": e["id"],
                    "label": e["name"],
                    "type": e["type"],
                    "description": e.get("description"),
                    "size": e.get("mention_count", 1),
                    "community_id": e.get("community_id"),
                    "document_ids": e.get("document_ids", [])
                })
                entity_ids.append(e["id"])
            
            # 관계 조회 (노드 간의 관계만)
            edges = []
            if entity_ids:
                relationships = self.memgraph.execute_read("""
                    MATCH (s:Entity)-[r:RELATES_TO]->(t:Entity)
                    WHERE s.id IN $entity_ids AND t.id IN $entity_ids
                    RETURN s.id as source_id, t.id as target_id,
                           r.type as relation_type, r.description as description,
                           r.weight as weight
                """, {"entity_ids": entity_ids})
                
                # 엣지 데이터 생성
                for i, r in enumerate(relationships):
                    edges.append({
                        "id": f"edge_{i}",
                        "source": r["source_id"],
                        "target": r["target_id"],
                        "label": r["relation_type"],
                        "description": r.get("description"),
                        "weight": r.get("weight", 1)
                    })
            
            # 커뮤니티 정보 조회
            communities = []
            community_ids = list(set([n.get("community_id") for n in nodes if n.get("community_id") is not None]))
            
            if community_ids:
                community_data = self.memgraph.execute_read("""
                    MATCH (c:Community {collection: $collection})
                    WHERE c.id IN $community_ids
                    RETURN c.id as id, c.summary as summary, c.member_count as member_count
                    ORDER BY c.member_count DESC
                """, {"collection": collection_name, "community_ids": community_ids})
                
                communities = [
                    {
                        "id": c["id"],
                        "summary": c.get("summary"),
                        "member_count": c.get("member_count")
                    }
                    for c in community_data
                ]
            
            # 통계
            stats = {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "community_count": len(communities)
            }
            
            return {
                "nodes": nodes,
                "edges": edges,
                "communities": communities,
                "stats": stats
            }
        except Exception:
            logger.exception("시각화 데이터 생성 중 오류가 발생했습니다.")
            return {
                "nodes": [],
                "edges": [],
                "communities": [],
                "stats": {"node_count": 0, "edge_count": 0, "community_count": 0}
            }


_graphrag_service: Optional[GraphRAGService] = None


def get_graphrag_service() -> GraphRAGService:
    """GraphRAG 서비스 인스턴스를 반환합니다.
    
    Returns:
        GraphRAGService: GraphRAG 서비스 인스턴스
    """
    
    global _graphrag_service
    if _graphrag_service is None:
        _graphrag_service = GraphRAGService(
            get_memgraph_manager(),
            get_vector_store_manager()
        )
    return _graphrag_service
