"""Seed synthetic reply history so the Analytics learned card shows its full
state for screenshots. Tagged for exact cleanup by unseed_learning_demo.py."""
import sys
sys.path.insert(0, 'src')
from datetime import datetime, timedelta
from adapix.db import get_session
from adapix.models import Campaign, Message

ORG = "steel-city-oms-demo"
with get_session() as s:
    camp = s.query(Campaign).filter(Campaign.practice_id == ORG).first()
    now = datetime.utcnow()
    for i in range(40):
        hour = [9, 10, 13, 16][i % 4]
        sent_at = (now - timedelta(days=2 + 2*i)).replace(hour=hour, minute=10, second=0, microsecond=0)
        s.add(Message(campaign_id=camp.id, direction="outbound", channel="sms",
                      body=f"seed {i}", status="sent", created_at=sent_at,
                      metadata_json={"screenshot_seed": True}))
        if (hour == 10 and i % 3 != 0) or (hour == 13 and i % 4 == 0) or (hour in (9, 16) and i % 7 == 0):
            s.add(Message(campaign_id=camp.id, direction="inbound", channel="sms",
                          body="sounds good", status="received",
                          created_at=sent_at + timedelta(hours=2),
                          metadata_json={"screenshot_seed": True}))
    s.commit()
print("seeded")
