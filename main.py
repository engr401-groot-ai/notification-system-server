import os
import re
import logging
from dotenv import load_dotenv
from fastapi import FastAPI, Query, Request
from google.cloud import bigquery, firestore

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("scraper")

# Initialize FastAPI
app = FastAPI(title="Notification System Server", version="1.0.0")

# Load environment variables
load_dotenv()

GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "your-gcp-project")
BQ_DATASET = os.getenv("BQ_DATASET", "your_dataset")
BQ_MENTIONS_TABLE = os.getenv("BQ_MENTIONS_TABLE", "mentions")


@app.get("/api/mentions/recent")
def get_recent_mentions(
	hours: int = Query(24, ge=1, description="How many hours back to fetch"),
):
	"""
	Returns mentions created in the last X hours.
	"""

	client = bigquery.Client(project=GCP_PROJECT_ID)
	table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_MENTIONS_TABLE}"

	sql = f"""
	SELECT video_name, keyword, text, video_url, start_sec, created_at
	FROM `{table_id}`
	WHERE CAST(created_at AS TIMESTAMP) > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @hours HOUR)
	ORDER BY created_at DESC
	"""

	job_config = bigquery.QueryJobConfig(
		query_parameters=[
			bigquery.ScalarQueryParameter("hours", "INT64", hours),
		]
	)

	rows = client.query(sql, job_config=job_config).result()

	results = []
	for r in rows:
		try:
			link = f"{r.video_url}&t={int(r.start_sec)}s"
		except Exception:
			link = getattr(r, "video_url", None)

		results.append({
			"video_name": getattr(r, "video_name", None),
			"keyword": getattr(r, "keyword", None),
			"text": getattr(r, "text", None),
			"video_url": getattr(r, "video_url", None),
			"link": link,
			"start_sec": getattr(r, "start_sec", None),
			"created_at": str(getattr(r, "created_at", None)),
		})

	return {"count": len(results), "results": results}


@app.get("/api/notification-settings")
def get_notification_settings():
	"""
	Fetch current settings from Firestore.
	Returns keys: sender, password, recipients (CSV string for easy editing).
	"""
	try:
		db = firestore.Client(database="notification-system")
		doc_ref = db.collection("settings").document("configuration")
		doc = doc_ref.get()

		if not doc.exists:
			return {
				"ok": True,
				"settings": {"sender": "", "password": "", "recipients": ""},
			}

		data = doc.to_dict() or {}

		# Convert Firestore Array -> CSV String for the UI text input
		recipients_list = data.get("recipients", [])
		recipients_str = ""
		if isinstance(recipients_list, list):
			recipients_str = ",".join(recipients_list)

		settings = {
			"sender": data.get("sender", ""),
			"password": data.get("password", ""),
			"recipients": recipients_str,
		}

		return {"ok": True, "settings": settings}

	except Exception as e:
		logger.error(f"Failed to fetch notification settings: {e}")
		return {"ok": False, "error": str(e)}


@app.post("/api/notification-settings")
async def update_notification_settings(request: Request):
	"""
	Update settings in Firestore: sender, password, recipients.
	"""
	try:
		body = await request.json()
		updates = {}

		# 1. Handle Sender
		if "sender" in body and body["sender"] is not None:
			val = str(body["sender"]).strip()
			if not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+", val) or "," in val:
				return {"ok": False, "error": "Sender must be a single valid email address."}
			updates["sender"] = val

		# 2. Handle Password
		if "password" in body and body["password"] is not None:
			val = str(body["password"]).strip()
			if any(c.isspace() for c in val):
				return {"ok": False, "error": "Password must not contain whitespaces."}
			if val:  # Only update if not empty
				updates["password"] = val

		# 3. Handle Recipients (String -> Array)
		if "recipients" in body and body["recipients"] is not None:
			val = str(body["recipients"]).strip()
			if " " in val:
				return {"ok": False, "error": "Recipients must be comma-separated with NO spaces."}

			# Convert CSV string to List
			email_list = [e.strip() for e in val.split(",") if e.strip()]

			for email in email_list:
				if "@" not in email:
					return {"ok": False, "error": f"Invalid email in recipients: {email}"}

			updates["recipients"] = email_list

		# Write to Firestore
		if updates:
			db = firestore.Client(database="notification-system")
			doc_ref = db.collection("settings").document("configuration")
			doc_ref.set(updates, merge=True)
			logger.info("Updated Firestore settings")

		return {"ok": True, "message": "Settings updated successfully"}

	except Exception as e:
		logger.error(f"Failed to update notification settings: {e}")
		return {"ok": False, "error": str(e)}