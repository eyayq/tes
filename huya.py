import time
import hmac
import hashlib
import re
import json
from curl_cffi import requests

class VidioStreamFetcher:
    def __init__(self, user_token: str, user_email: str = "guest"):
        """
        Inisialisasi dengan token user Vidio.
        Token dapat diambil dari header 'X-User-Token' saat browsing vidio.com di browser.
        """
        self.user_token = user_token
        self.user_email = user_email
        self.session = requests.Session(impersonate="chrome")
        
        # Header standar yang sering digunakan oleh klien Vidio
        self.base_headers = {
            "Referer": "https://vidio.com/",
            "User-Agent": "tv-android/2.56.10 (852)",
            "X-Api-Platform": "tv-android",
            "X-Secure-Level": "2",
            "X-Api-Auth": "laZOmogezono5ogekaso5oz4Mezimew1", # Kunci API statis yang umum ditemukan
            "X-User-Token": self.user_token,
            "X-User-Email": self.user_email,
        }

    def _generate_signature(self, timestamp: int) -> str:
        """
        Menghasilkan X-Signature.
        Catatan: Algoritma ini berdasarkan reverse-engineering komunitas.
        Format umum yang ditemukan: HMAC-SHA256 dengan kunci rahasia.
        Kunci rahasia sering kali berupa kombinasi 'V1d10D3v' + timestamp atau kunci statis lain.
        Jika ini gagal, berarti Vidio mengubah algoritma atau kunci dinamis.
        """
        # Contoh implementasi berdasarkan skrip komunitas (riswanfile52-sys/vidio)
        # Kunci rahasia mungkin bervariasi, ini adalah pola yang umum ditemukan
        secret_key = f"V1d10D3v:{timestamp}".encode('utf-8')
        message = str(timestamp).encode('utf-8')
        
        signature = hmac.new(secret_key, message, hashlib.sha256).hexdigest()
        return signature

    def get_stream_url(self, stream_id: str) -> str:
        """
        Mengambil URL HLS stream dari ID stream.
        """
        # 1. Dapatkan timestamp saat ini
        t = int(time.time())
        
        # 2. Generate signature
        signature = self._generate_signature(t)
        
        # 3. Siapkan header lengkap
        headers = self.base_headers.copy()
        headers.update({
            "X-Client": str(t),
            "X-Signature": signature,
        })

        # 4. Endpoint API untuk mendapatkan detail stream
        # Endpoint ini mengembalikan JSON yang berisi URL HLS
        api_url = f"https://api.vidio.com/livestreamings/{stream_id}/stream?initialize=true"
        
        try:
            response = self.session.get(api_url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                # Ekstrak URL HLS dari respons JSON
                # Struktur JSON biasanya: data.attributes.hls
                hls_url = data.get('data', {}).get('attributes', {}).get('hls')
                
                if hls_url:
                    return hls_url
                else:
                    print("HLS URL tidak ditemukan dalam respons.")
                    print("Respons JSON:", json.dumps(data, indent=2))
                    return None
            else:
                print(f"Gagal mendapatkan stream. Status Code: {response.status_code}")
                print(f"Respons: {response.text}")
                return None
                
        except Exception as e:
            print(f"Terjadi kesalahan: {e}")
            return None

    def get_channel_list(self) -> list:
        """
        (Opsional) Mengambil daftar channel live.
        Endpoint ini mungkin memerlukan parameter tambahan atau cookie 'luws'.
        """
        # Endpoint untuk mendapatkan daftar livestream   
