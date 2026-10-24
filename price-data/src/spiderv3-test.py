import asyncio
import json
import os
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

DEFAULT_WINDOWS_CHROME_PATH = "C:/Program Files/Google/Chrome/Application/chrome.exe"
results = []  # 全局存储结果
last_response_time = None  # 记录上一次接口响应时间


def get_browser_executable_path():
    configured_path = os.environ.get("KLOOK_CHROME_EXECUTABLE")
    if configured_path:
        return configured_path

    if os.name == "nt" and os.path.exists(DEFAULT_WINDOWS_CHROME_PATH):
        return DEFAULT_WINDOWS_CHROME_PATH

    return None


def process_data(data):
    if isinstance(data, dict) and "items" in data:
        return data["items"]
    return data

def save_to_file(data, filename="output.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[✔] 数据已写入 {filename}")

async def generate():
    global last_response_time

    async with async_playwright() as playwright:
        launch_options = {
            "user_data_dir": "userdata",
            "headless": False,
            "args": [
                '--incognito',
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-blink-features",
                "--start-maximized",
            ],
        }
        executable_path = get_browser_executable_path()
        if executable_path:
            launch_options["executable_path"] = executable_path

        browser = await playwright.chromium.launch_persistent_context(**launch_options)

        page = await browser.new_page()

        async def on_response(response):
            global last_response_time
            url = response.url
            if 'transfercarrentalapisrv/product/result' in url:
                last_response_time = datetime.now()
                print(f"[+] 接口触发: {url}")
                try:
                    json_data = await response.json()
                    processed = process_data(json_data)
                    results.append(processed)
                    save_to_file(results)
                except Exception as e:
                    print("解析接口时出错：", e)

        page.on("response", on_response)

        await page.goto(
            "https://www.klook.com/zh-CN/car-rentals/results/?pick=%E4%B8%9C%E4%BA%AC%E5%9B%BD%E9%99%85%E6%9C%BA%E5%9C%BA&drop=&diffLoc=&pDate=2025-12-02%2010%3A00&dDate=2025-12-05%2010%3A00&age=30&lat=35.552258&long=139.779694&dLat=&dLong=&code=JP&dCode=&iata=HND&dIata=&pCityId=28&pCityName=%E4%B8%9C%E4%BA%AC&pPoiId=50036510&dCityId=&dCityName=&dPoiId=&promoKey=&supplierId=&spm=CarRental_SearchResult.SearchBtn&clickId=203c493ba9"
        )
        await page.wait_for_load_state("networkidle")

        # 直接通过 evaluate 修改
        page.evaluate("""
            localStorage.setItem('carRentalCountry', '{"localData":"CN","expires":1764548305840}');
        """)
        
        # 立即验证
        result = page.evaluate("localStorage.getItem('carRentalCountry')")
        print(f"直接设置结果: {result}")
        
        # 方法2: 重新加载确认持久化
        page.reload()
        result_after_reload = page.evaluate("localStorage.getItem('carRentalCountry')")
        print(f"重新加载后: {result_after_reload}")
        await page.wait_for_load_state("networkidle")


        # 每隔2秒滚动一次，直到接口 5 秒内没有触发
        while True:
            await page.evaluate("window.scrollBy(0, window.innerHeight);")
            await asyncio.sleep(2)

            if last_response_time is not None:
                elapsed = (datetime.now() - last_response_time).total_seconds()
                if elapsed > 50:
                    print("[✔] 页面接口已经停止触发，准备关闭浏览器")
                    break
            else:
                # 如果还没触发过接口，也继续滚动
                continue

        await browser.close()

if __name__ == "__main__":
    asyncio.run(generate())
