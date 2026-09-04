def foo(x, y=10):
    return x + y

class Bar:
    def method(self, z: str = "hello") -> str:
        return z.upper()