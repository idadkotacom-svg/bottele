import requests

# 1. ISI DENGAN DATA ANDA:
APP_ID = "1467908154690833"
APP_SECRET = "8443afd029cce5250759b6430baa3ccb"
SHORT_LIVED_TOKEN = "EAAU3DgAfjREBQztxmKZAkc0MadthMrPDBTiFw2ESLvlTCShM8kPLgZBmm51Ydx8fnNCiaswmKlzLQmrQ6E2JOH3OUlB5S1vZB0vJXyc6nzZBVQgZB1xxVw2J4ZBKCZBgQ0oZC4HlAN8TuXAqvZBorcFMCWa6dnksEo8UdaAYvAb7kn8QKtfZBQAKiT8MCNg1gZAmefEYLag00HlbzlZAlzxlq8VAsoRwMcu2w92ZBQL5PZCNI7dPbqTboahTnOS322ZAqMXy2sou7TZC05Tc1968HtZBwvfAKGAZDZD"
PAGE_ID = "590645608285622"

def get_never_expiring_page_token():
    print("Meminta Long-Lived User Token...")
    # Langkah 1: Tukar Token Pendek (1 jam) menjadi Token Panjang (60 Hari)
    url_1 = f"https://graph.facebook.com/v19.0/oauth/access_token?grant_type=fb_exchange_token&client_id={APP_ID}&client_secret={APP_SECRET}&fb_exchange_token={SHORT_LIVED_TOKEN}"
    res_1 = requests.get(url_1).json()
    
    if "error" in res_1:
        print("❌ Error Langkah 1:", res_1["error"]["message"])
        return
        
    long_lived_user_token = res_1["access_token"]
    print("✅ Berhasil dapat Long-Lived User Token!")

    # Langkah 2: Tukar Token Panjang (User) menjadi Token Abadi (Page)
    print("\nMeminta Never-Expiring Page Token...")
    url_2 = f"https://graph.facebook.com/v19.0/{PAGE_ID}?fields=access_token&access_token={long_lived_user_token}"
    res_2 = requests.get(url_2).json()
    
    if "error" in res_2:
        print("❌ Error Langkah 2:", res_2["error"]["message"])
        return
        
    page_access_token = res_2["access_token"]
    print("\n🎉 BERHASIL! INI PAGE ACCESS TOKEN ABADI ANDA:")
    print("--------------------------------------------------")
    print(page_access_token)
    print("--------------------------------------------------")
    print("Simpan token ini di .env sebagai FB_PAGE_ACCESS_TOKEN!")

if __name__ == "__main__":
    get_never_expiring_page_token()
