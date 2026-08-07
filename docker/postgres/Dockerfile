# PostGIS 이미지에 pgvector 확장을 추가한다 (FAQ·학칙/규정 RAG 임베딩 검색용).
# postgis/postgis 이미지는 PGDG(PostgreSQL Global Development Group) apt 저장소를
# 이미 설정하고 있어, 별도 저장소 추가 없이 apt로 바로 설치할 수 있다.
FROM postgis/postgis:16-3.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-pgvector \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
