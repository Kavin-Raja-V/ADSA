from pathlib import Path
import json
from cas_engine import CHUNK_SIZE
from merkle_tree import build_merkle_tree

class StorageEngine:
    def __init__(self,cas,avl):
        self.cas=cas;self.avl=avl;self.path=Path(cas.directory).parent/"files.json"
        self.files=json.loads(self.path.read_text()) if self.path.exists() else {}
        for k,v in self.files.items():self.avl.insert(k,v)
    def save(self):self.path.write_text(json.dumps(self.files,indent=2))
    def add_file(self,name,data):
        path=f"/uploads/{len(self.files)+1:02d}-{name.replace(' ','_')}"
        hashes=[self.cas.put(data[i:i+CHUNK_SIZE]) for i in range(0,len(data),CHUNK_SIZE)]
        if not hashes:hashes=[self.cas.put(b"")]
        entry={"name":name,"path":path,"size":len(data),"block_hashes":hashes,
               "merkle_root":build_merkle_tree(hashes),"blocks":len(hashes),"status":"unverified"}
        self.files[path]=entry;self.avl.insert(path,entry);self.save();return entry
    def verify(self,path):
        e=self.files[path]
        current=[self.cas.sha256(self.cas.get(h)) for h in e["block_hashes"]]
        root=build_merkle_tree(current);ok=root==e["merkle_root"]
        e["status"]="verified intact" if ok else "tamper detected";self.save()
        return {"ok":ok,"path":path,"recorded_root":e["merkle_root"],
                "recomputed_root":root,"bad_blocks":[h for h,r in zip(e["block_hashes"],current) if h!=r]}
    def delete(self,path):
        e=self.files.pop(path)
        for h in e["block_hashes"]:self.cas.decrement(h)
        self.avl.delete(path);self.save();return {"deleted":path}
    @staticmethod
    def fmt(n):
        if n<1024:return f"{n} B"
        if n<1048576:return f"{n/1024:.1f} KB"
        return f"{n/1048576:.2f} MB"
    def state(self):
        logical=sum(e["size"] for e in self.files.values())
        physical=sum(m["size"] for m in self.cas.meta.values())
        saved=max(logical-physical,0)
        return {"stats":{"files":len(self.files),"unique_blocks":len(self.cas.meta),
          "logical_size":self.fmt(logical),"physical_size":self.fmt(physical),
          "saved_bytes":self.fmt(saved),"saved_percent":round(saved/logical*100,1) if logical else 0},
          "blocks":[{"hash":h,**m} for h,m in self.cas.meta.items()],
          "files":list(self.files.values()),
          "avl":[{"path":n.key,"height":n.height} for n in self.avl.inorder()]}
