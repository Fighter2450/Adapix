import sys, json
sys.path.insert(0, 'src')
from datetime import datetime, timedelta
from adapix.db import get_session
from adapix.models import Campaign, Message, Patient
from adapix import learning

ORG = "steel-city-oms-demo"

# 1) baseline stats on demo data
s0 = learning.stats(ORG)
print("baseline:", json.dumps({k: s0[k] for k in ("sends","replies","enough_data","best_hour","best_channel","best_weekday")}))

# 2) seed synthetic history: 30 sends spread across hours, replies clustered at 10am
with get_session() as s:
    camp = s.query(Campaign).filter(Campaign.practice_id == ORG).first()
    pat = s.get(Patient, camp.patient_id)
    now = datetime.utcnow()
    added = []
    for i in range(30):
        hour = [9, 10, 14, 17][i % 4]
        day = now - timedelta(days=3 + i)
        sent_at = day.replace(hour=hour, minute=5, second=0, microsecond=0)
        m = Message(campaign_id=camp.id, direction="outbound", channel="sms",
                    body=f"synthetic send {i}", status="sent", created_at=sent_at,
                    metadata_json={"synthetic_learning_test": True})
        s.add(m); added.append(m)
        # 10am sends reply 80%, others 10%
        if (hour == 10 and i % 5 != 0) or (hour != 10 and i % 8 == 0):
            r = Message(campaign_id=camp.id, direction="inbound", channel="sms",
                        body="ok sounds good", status="received",
                        created_at=sent_at + timedelta(hours=3),
                        metadata_json={"synthetic_learning_test": True})
            s.add(r); added.append(r)
    s.commit()

s1 = learning.stats(ORG)
print("seeded:", json.dumps({k: s1[k] for k in ("sends","replies","enough_data","best_hour","best_hour_rate","best_channel","best_weekday")}))
print("insight:", s1["insight"])
assert s1["enough_data"], "should have enough data"
assert s1["best_hour"] == 10, f"expected best hour 10, got {s1['best_hour']}"
no = learning.next_occurrence(10)
print("next 10am occurrence:", no, "(in future:", no > datetime.utcnow(), ")")

# cleanup synthetic rows
with get_session() as s:
    n = 0
    for m in s.query(Message).filter(Message.campaign_id == camp.id).all():
        if (m.metadata_json or {}).get("synthetic_learning_test"):
            s.delete(m); n += 1
    s.commit()
print("cleaned", n, "synthetic messages")
s2 = learning.stats(ORG)
print("post-cleanup matches baseline:", s2["sends"] == s0["sends"])
