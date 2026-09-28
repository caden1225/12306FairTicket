import base64

from gmssl.sm4 import SM4_ENCRYPT, CryptSM4

# Key hard-coded in 12306 login page JS (SM4/ECB/PKCS7, base64, "@" prefix).
_SM4_KEY = b"tiekeyuankp12306"


def encrypt_password(password: str) -> str:
    sm4 = CryptSM4()
    sm4.set_key(_SM4_KEY, SM4_ENCRYPT)
    cipher = sm4.crypt_ecb(password.encode("utf-8"))
    return "@" + base64.b64encode(cipher).decode("ascii")
