import hashlib

def hash_pair(left,right):
    return hashlib.sha256((left+right).encode()).hexdigest()

def build_merkle_tree(hashes):
    level=list(hashes)
    if not level:return None
    while len(level)>1:
        if len(level)%2: level.append(level[-1])
        level=[hash_pair(level[i],level[i+1]) for i in range(0,len(level),2)]
    return level[0]
