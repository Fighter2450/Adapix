import sys
sys.path.insert(0, 'src')
from adapix.db import get_session
from adapix.models import Campaign, Message
ORG = "steel-city-oms-demo"
with get_session() as s:
    cids = [c.id for c in s.query(Campaign).filter(Campaign.practice_id == ORG).all()]
    n = 0
    for m in s.query(Message).filter(Message.campaign_id.in_(cids)).all():
        if (m.metadata_json or {}).get("screenshot_seed"):
            s.delete(m); n += 1
    s.commit()
print("removed", n)
