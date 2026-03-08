from flask import Flask, request, jsonify
from flask_cors import CORS
from iqoptionapi.stable_api import IQ_Option
import time

app = Flask(__name__)
CORS(app)  # Permite acesso de qualquer site

@app.route('/connect', methods=['POST'])
def connect():
    try:
        data = request.json
        email = data['email']
        senha = data['senha']
        account = data.get('account', 'PRACTICE')
        
        print(f"Conectando: {email}")
        
        Iq = IQ_Option(email, senha)
        Iq.change_balance(account)
        check, reason = Iq.connect()
        
        if check:
            return jsonify({
                "status": "success",
                "message": "Conectado com sucesso!",
                "balance": Iq.get_balance(),
                "account": account
            })
        else:
            return jsonify({
                "status": "error", 
                "message": f"Falha na conexão: {reason}"
            }), 400
            
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/balance', methods=['POST'])
def balance():
    try:
        data = request.json
        email = data['email']
        senha = data['senha']
        # Recupera saldo (precisa manter sessão - simplificado)
        return jsonify({"balance": "Funciona!"})
    except:
        return jsonify({"error": "Erro"}), 500

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "bot": "IQ Option API Multi-usuário",
        "endpoints": ["/connect (POST)", "/balance (POST)"],
        "uso": "POST com {'email': '', 'senha': '', 'account': 'PRACTICE'}"
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
