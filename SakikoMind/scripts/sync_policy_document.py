"""按 source_id 将演示政策 JSON 的单条文档同步到现有 ChromaDB。"""
import argparse
import json
import os
import pathlib

import chromadb


def main() -> int:
    parser = argparse.ArgumentParser(description="同步单条 SakikoMind 演示政策")
    parser.add_argument("--source-id", required=True)
    parser.add_argument(
        "--policies",
        default="/app/data/demo_docs/saas_policies.json",
    )
    args = parser.parse_args()

    policies = json.loads(pathlib.Path(args.policies).read_text(encoding="utf-8"))
    matches = [document for document in policies if document.get("source_id") == args.source_id]
    if len(matches) != 1:
        raise RuntimeError(f"政策文件中 source_id={args.source_id} 的记录数不是 1")
    document = matches[0]

    client = chromadb.HttpClient(
        host=os.getenv("CHROMA_HOST", "chromadb"),
        port=int(os.getenv("CHROMA_PORT", "8000")),
    )
    collection = client.get_collection("knowledge_base")
    existing = collection.get(where={"source_id": args.source_id})
    if len(existing.get("ids", [])) != 1:
        raise RuntimeError(f"知识库中 source_id={args.source_id} 的记录数不是 1")

    metadata = {
        "source_id": document["source_id"],
        "title": document["title"],
        "chunk_index": 0,
        "total_chunks": 1,
        "version": document["version"],
        "effective_date": document["effective_date"],
        "scope": document["scope"],
    }
    collection.update(
        ids=existing["ids"],
        documents=[document["content"]],
        metadatas=[metadata],
    )
    print(
        json.dumps(
            {
                "source_id": args.source_id,
                "updated": 1,
                "version": document["version"],
                "effective_date": document["effective_date"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
