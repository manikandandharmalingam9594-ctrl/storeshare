from backend.database import get_provider_reconciliation_records
import json

print("=== RECONCILIATION FOR NODE 10 (local_provider@storeshare.io) ===")
rep10 = get_provider_reconciliation_records(10)
print(json.dumps(rep10, default=str, indent=2))

print("\n=== RECONCILIATION FOR NODE 2 (24d126@psgitech.ac.in) ===")
rep2 = get_provider_reconciliation_records(2)
print(json.dumps(rep2, default=str, indent=2))
