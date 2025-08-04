#!/usr/bin/env python
from flask import Flask, request, jsonify
import os
import sys
import json
import logging
from datetime import datetime
from typing import Dict, Any

# Add the pipeline directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from pipeline.main import process_external_pipeline_files

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook_endpoint():
    """Webhook endpoint for receiving files from external pipelines"""
    try:
        # Get the webhook data
        webhook_data = request.get_json()
        
        if not webhook_data:
            logger.error("No JSON data provided")
            return jsonify({
                "error": "No JSON data provided",
                "status": "failed"
            }), 400
        
        # Extract files data
        data = webhook_data.get('data', {})
        
        if not data or 'files' not in data:
            logger.error("Missing files data in webhook")
            return jsonify({
                "error": "Missing files data in webhook",
                "status": "failed"
            }), 400
        
        project_name = data.get('project_name', 'unknown-project')
        files_dict = data.get('files', {})
        
        logger.info(f"Received webhook for project '{project_name}' with {len(files_dict)} files")
        
        # Process the files through your pipeline
        result = process_external_pipeline_files(data)
        
        logger.info(f"Pipeline processing completed for project '{project_name}': {result}")
        
        return jsonify({
            "status": "received",
            "result": result,
            "timestamp": datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}")
        return jsonify({
            "error": f"Internal server error: {str(e)}",
            "status": "failed"
        }), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "pipeline-webhook"
    })

if __name__ == '__main__':
    # Get port from environment variable or use default
    port = int(os.environ.get('PIPELINE_WEBHOOK_PORT', 5000))
    host = os.environ.get('PIPELINE_WEBHOOK_HOST', '0.0.0.0')
    
    logger.info(f"Starting Pipeline Webhook server on {host}:{port}")
    
    app.run(
        host=host,
        port=port,
        debug=False,
        threaded=True
    )