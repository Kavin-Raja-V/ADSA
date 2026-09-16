from pathlib import Path
import hashlib, json, random
CHUNK_SIZE = 64 * 1024

class CASEngine:
    def __init__(self, directory):
        self.directory=Path(directory); self.directory.mkdir(parents=True,exist_ok=True)
        self.meta_path=self.directory.parent/"cas_metadata.json"
        self.meta=json.loads(self.meta_path.read_text()) if self.meta_path.exists() else {}
        self.save()
    def save(self): self.meta_path.write_text(json.dumps(self.meta,indent=2))
    @staticmethod
    def sha256(data): return hashlib.sha256(data).hexdigest()
    def put(self,data):
        h=self.sha256(data)
        if h in self.meta:
            self.meta[h]["ref_count"]+=1
        else:
            (self.directory/h).write_bytes(data)
            self.meta[h]={"size":len(data),"ref_count":1,"corrupted":False}
        self.save(); return h
    def get(self,h): return (self.directory/h).read_bytes()
    def decrement(self,h):
        if h not in self.meta:return
        self.meta[h]["ref_count"]-=1
        if self.meta[h]["ref_count"]<=0:
            try:(self.directory/h).unlink()
            except FileNotFoundError:pass
            del self.meta[h]
        self.save()
    def corrupt(self,h):
        data=bytearray(self.get(h))
        if not data:return False
        data[random.randrange(len(data))]^=0b00010000
        (self.directory/h).write_bytes(data)
        self.meta[h]["corrupted"]=True; self.save(); return True
