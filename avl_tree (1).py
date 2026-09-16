class Node:
    def __init__(self,key,data):
        self.key=key; self.data=data; self.left=None; self.right=None; self.height=1

class AVLTree:
    def __init__(self): self.root=None
    def height(self,n): return n.height if n else 0
    def balance(self,n): return self.height(n.left)-self.height(n.right) if n else 0
    def update(self,n): n.height=1+max(self.height(n.left),self.height(n.right))
    def rotate_right(self,y):
        x=y.left;t=x.right;x.right=y;y.left=t;self.update(y);self.update(x);return x
    def rotate_left(self,x):
        y=x.right;t=y.left;y.left=x;x.right=t;self.update(x);self.update(y);return y
    def _insert(self,n,key,data):
        if not n:return Node(key,data)
        if key==n.key:n.data=data;return n
        if key<n.key:n.left=self._insert(n.left,key,data)
        else:n.right=self._insert(n.right,key,data)
        self.update(n);b=self.balance(n)
        if b>1 and key<n.left.key:return self.rotate_right(n)
        if b<-1 and key>n.right.key:return self.rotate_left(n)
        if b>1 and key>n.left.key:n.left=self.rotate_left(n.left);return self.rotate_right(n)
        if b<-1 and key<n.right.key:n.right=self.rotate_right(n.right);return self.rotate_left(n)
        return n
    def insert(self,key,data):self.root=self._insert(self.root,key,data)
    def delete(self,key):
        items=[n for n in self.inorder() if n.key!=key];self.root=None
        for n in items:self.insert(n.key,n.data)
    def inorder(self):
        result=[]
        def walk(n):
            if n:walk(n.left);result.append(n);walk(n.right)
        walk(self.root);return result
