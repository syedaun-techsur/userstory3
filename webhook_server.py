from flask import Flask, request, jsonify
import hmac
import hashlib
import os
import json
from pr_watcher import PRWatcher

app = Flask(__name__)
GITHUB_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")  # Set this in your environment

watcher = PRWatcher()  # You may want to refactor PRWatcher to allow stateless usage

def verify_signature(payload, signature, secret):
    mac = hmac.new(secret.encode(), msg=payload, digestmod=hashlib.sha256)
    expected = 'sha256=' + mac.hexdigest()
    return hmac.compare_digest(expected, signature)

@app.route("/webhook", methods=["POST"])
def github_webhook():
    print("Webhook received!")
    signature = request.headers.get("X-Hub-Signature-256")
    if GITHUB_SECRET and signature:
        if not verify_signature(request.data, signature, GITHUB_SECRET):
            return "Invalid signature", 400

    event = request.headers.get("X-GitHub-Event")
    payload = request.get_json(silent=True)
    if not payload:
        return "No JSON payload", 400

    # Only process pull_request opened events
    if event == "pull_request":
        action = payload.get("action")
        if action == "opened":
            pr_branch = payload["pull_request"]["head"]["ref"]
            if pr_branch.startswith("ai_refined_code_"):
                print(f"Ignoring PR from ai-refined branch to prevent loop: {pr_branch}")
                return jsonify({"msg": "Ignored AI-generated PR"}), 200
            repo_name = payload["repository"]["full_name"]
            pr_number = payload["pull_request"]["number"]
            pr_title = payload["pull_request"]["title"]
            main_branch = payload["pull_request"]["base"]["ref"]
            # Call the new handler to process all files in the PR
            watcher.handle_new_pr(repo_name, pr_number, pr_title, pr_branch, main_branch)
            return jsonify({"msg": "Processed PR for all files"}), 200
        else:
            return jsonify({"msg": "Event ignored (not opened)"}), 200
    else:
        return jsonify({"msg": "Event ignored (not pull_request)"}), 200

    return jsonify({"msg": "Processed"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050)