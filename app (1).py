from flask import Flask,request,jsonify,send_from_directory
from flask_cors import CORS
from pathlib import Path
from cas_engine import CASEngine
from avl_tree import AVLTree
from storage_engine import StorageEngine

BASE=Path(__file__).resolve().parent.parent
app=Flask(__name__);CORS(app)
cas=CASEngine(BASE/"storage"/"blocks")
avl=AVLTree()
store=StorageEngine(cas,avl)

@app.get("/")
def home():return send_from_directory(BASE/"frontend","index.html")

@app.get("/api/state")
def state():return jsonify(store.state())

@app.post("/api/upload")
def upload():
    f=request.files.get("file")
    if not f:return jsonify(error="No file supplied"),400
    return jsonify(store.add_file(f.filename,f.read()))

@app.post("/api/verify")
def verify():
    try:return jsonify(store.verify(request.json["path"]))
    except KeyError:return jsonify(error="File not found"),404

@app.post("/api/delete")
def delete():
    try:return jsonify(store.delete(request.json["path"]))
    except KeyError:return jsonify(error="File not found"),404

@app.post("/api/corrupt")
def corrupt():
    try:
        changed=cas.corrupt(request.json["hash"])
        return jsonify(ok=changed)
    except KeyError:return jsonify(error="Block not found"),404

if __name__=="__main__":
    app.run(host="127.0.0.1",port=5000,debug=True)
