from flask import Flask, jsonify
import os

app = Flask(__name__)

@app.route('/health', methods=['GET'])
def health():
    return "OK"

@app.route('/', methods=['GET'])
def home():
    return "Bot IQ Option Online!"

@app.route('/connect', methods=['POST'])
def connect():
    return {"status": "POST funciona!"}

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
