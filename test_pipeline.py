import sys, logging
logging.basicConfig(level=logging.DEBUG, stream=sys.stdout, force=True)

print("=== Testing graph invocation ===")
from pipelines.graph import get_graph

doc_id = "36f66a05-61f3-4b67-9099-14db9e1547ab"
config = {"configurable": {"thread_id": doc_id}}
initial_state = {
    "document_id": doc_id,
    "filename": "driving_license_real_train_000001.jpeg",
    "object_key": "raw/36f66a05-61f3-4b67-9099-14db9e1547ab.jpeg",
    "retry_count": 0,
    "extracted_fields": {},
    "validation_issues": [],
}
print("invoking graph...")
try:
    result = get_graph().invoke(initial_state, config=config)
    print("graph done, result keys:", list(result.keys()) if result else "EMPTY")
except Exception as e:
    print("EXCEPTION:", e)
    import traceback; traceback.print_exc()
