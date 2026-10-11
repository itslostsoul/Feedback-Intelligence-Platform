import json
import random
from datetime import datetime, timedelta, timezone

# Real-world B2B SaaS personas and sources
sources = ["zendesk", "intercom", "salesforce", "g2_reviews", "slack_connect"]
tiers = ["enterprise", "growth", "startup", "free_tier"]

# Highly specific, authentic B2B SaaS feedback
real_world_complaints = [
    # Auth & Security
    "SAML SSO is failing for our Azure AD users. It keeps looping back to the login screen.",
    "We need SCIM provisioning support before we can roll this out to the rest of the org.",
    "The RBAC (Role-Based Access Control) is too rigid. I can't give a user 'View Only' access to billing without exposing API keys.",
    "Session timeouts are way too short. My team is getting logged out every 15 minutes.",
    # Integrations & API
    "The Jira integration drops custom fields when syncing tickets. It only pulls over the title and description.",
    "We are hitting the API rate limit of 100/min way too fast. We need a higher tier or a bulk endpoint.",
    "Your webhooks keep silently failing. We aren't getting any 500 error logs on our end, the payloads just never arrive.",
    "The Salesforce sync is duplicating contacts. We have 400 messy records to clean up now.",
    # Performance
    "Exporting the Q3 analytics report to CSV timed out after 3 minutes. It's completely unusable for large datasets.",
    "The main dashboard takes 8-10 seconds to load for accounts with over 10,000 active users.",
    "Typing in the rich text editor has a noticeable 2-second lag when the document gets long.",
    # UI / UX
    "The new navigation sidebar buried the API settings. It took me 20 minutes to find where to rotate my keys.",
    "Bulk deleting records is a nightmare. I have to click a checkbox on every single row instead of 'Select All'.",
    "Dark mode is broken on the billing invoice page. The text is dark gray on a black background.",
    # Billing & Compliance
    "Our accounting team needs invoices sent to a separate billing email automatically.",
    "We need a SOC2 Type II report and a DPA before we can renew our annual contract.",
    "The usage-based pricing dashboard hasn't updated in 48 hours. I have no idea what our current spend is."
]

records = []
now = datetime.now(timezone.utc)

for i in range(1, 501):
    # Inject slight variations to make them unique for the vector database
    base_text = random.choice(real_world_complaints)
    variation_tags = [
        " Please escalate this.", " It's blocking our deployment.", 
        " Any ETA on a fix?", " We noticed this starting yesterday.", 
        " Attached is the HAR file.", " Support ticket #402 opened.", ""
    ]
    raw_text = base_text + random.choice(variation_tags)
    
    # Skew dates to create realistic volume spikes for the trend charts
    days_ago = random.choices(
        population=[random.randint(0, 5), random.randint(6, 15), random.randint(16, 30)],
        weights=[0.6, 0.3, 0.1] # 60% of data is from the last 5 days
    )[0]
    
    created_at = now - timedelta(days=days_ago, hours=random.randint(0, 23))
    
    records.append({
        "external_id": f"TKT-{10000 + i}",
        "source": random.choice(sources),
        "source_type": "support_ticket" if "zendesk" in sources else "other",
        "customer_id": f"ORG-{random.randint(10, 99)}",
        "customer_tier": random.choices(tiers, weights=[0.2, 0.5, 0.2, 0.1])[0],
        "raw_text": raw_text,
        "created_at": created_at.isoformat()
    })

with open("enterprise_500_records.json", "w") as f:
    json.dump(records, f, indent=2)

print("✅ Generated enterprise_500_records.json with dense B2B data!")