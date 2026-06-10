"""
AnÃ¡lise profunda da loja B_CLOUSET - Script one-shot
Executa no Railway onde TOKENS_JSON estÃ¡ disponÃ­vel
"""
import os, json, time, hmac, hashlib, requests
from datetime import datetime, timedelta

PARTNER_ID  = int(os.environ.get("SHOPEE_PARTNER_ID", "2036153"))
PARTNER_KEY = os.environ.get("SHOPEE_PARTNER_KEY", "")
BASE_URL    = "https://partner.shopeemobile.com"
SHOP_ID     = 678623539  # B_CLOUSET

def carregar_token():
    tj = os.environ.get("TOKENS_JSON", "")
    if tj:
        tokens = json.loads(tj)
        bc = tokens.get("b_clouset", {})
        # shop_id pode vir do token ou hardcoded
        access = bc.get("access_token", "")
        refresh = bc.get("refresh_token", "")
        shop = bc.get("shop_id") or SHOP_ID
        return access, refresh, int(shop)
    return "", "", SHOP_ID

def sign(path, ts, access_token="", shop_id=0):
    if access_token and shop_id:
        base = f"{PARTNER_ID}{path}{ts}{access_token}{shop_id}"
    else:
        base = f"{PARTNER_ID}{path}{ts}"
    return hmac.new(PARTNER_KEY.encode(), base.encode(), hashlib.sha256).hexdigest()

def api(path, params, access_token, shop_id):
    ts = int(time.time())
    sig = sign(path, ts, access_token, shop_id)
    p = {"partner_id": PARTNER_ID, "timestamp": ts, "sign": sig,
         "access_token": access_token, "shop_id": shop_id, **params}
    url = BASE_URL + path
    try:
        r = requests.get(url, params=p, timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def api_post(path, body, access_token, shop_id):
    ts = int(time.time())
    sig = sign(path, ts, access_token, shop_id)
    p = {"partner_id": PARTNER_ID, "timestamp": ts, "sign": sig,
         "access_token": access_token, "shop_id": shop_id}
    url = BASE_URL + path
    try:
        r = requests.post(url, params=p, json=body, timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def main():
    access_token, refresh_token, shop_id = carregar_token()
    print(f"\n{'='*60}")
    print(f"ANÃLISE PROFUNDA: B_CLOUSET (shop_id: {shop_id})")
    print(f"{'='*60}")
    print(f"Token disponÃ­vel: {'SIM' if access_token else 'NÃO'}")

    if not access_token:
        print("â Sem access_token â abortando")
        return

    now = int(time.time())
    d15  = now - 15*86400
    d30  = now - 30*86400
    d60  = now - 60*86400

    # 1. INFO DA LOJA
    print("\n--- 1. INFO E RATING DA LOJA ---")
    info = api("/api/v2/shop/get_shop_info", {}, access_token, shop_id)
    if "response" in info:
        r = info["response"]
        print(f"  Nome: {r.get('shop_name', '?')}")
        print(f"  Status: {r.get('status', '?')}")
        print(f"  Rating: {r.get('rating_star', '?')}")
        print(f"  Seguidores: {r.get('follower_count', '?')}")
        print(f"  Produtos ativos: {r.get('item_count', '?')}")
    else:
        print(f"  Resposta: {json.dumps(info)[:200]}")

    # 2. PERFORMANCE DA LOJA (response rate, chat)
    print("\n--- 2. PERFORMANCE & ATENDIMENTO ---")
    perf = api("/api/v2/shop/get_shop_performance", {}, access_token, shop_id)
    if "response" in perf:
        r = perf["response"]
        overall = r.get("overall_performance", {})
        print(f"  Taxa resposta chat: {overall.get('response_rate', {}).get('point', '?')}%")
        print(f"  Tempo resposta: {overall.get('average_response_time', {}).get('point', '?')}h")
        print(f"  AvaliaÃ§Ã£o loja: {overall.get('rating_bad', {}).get('point', '?')} ruim / {overall.get('rating_good', {}).get('point', '?')} bom")
        print(f"  Taxa prep a tempo: {overall.get('preparation_time', {}).get('point', '?')}%")
        print(f"  Taxa nÃ£o envio: {overall.get('seller_did_not_ship', {}).get('point', '?')}%")
        # Check penalty levels
        for key, val in overall.items():
            level = val.get("level") if isinstance(val, dict) else None
            if level and level not in ("normal", "good"):
                print(f"  â ï¸  {key}: NÃVEL {level}")
    else:
        print(f"  Resposta: {json.dumps(perf)[:300]}")

    # 3. PEDIDOS ÃLTIMOS 30 DIAS
    print("\n--- 3. PEDIDOS ÃLTIMOS 30 DIAS ---")
    all_orders = []
    offset = 0
    while True:
        resp = api("/api/v2/order/get_order_list", {
            "time_range_field": "create_time",
            "time_from": d30, "time_to": now,
            "page_size": 100, "cursor": str(offset),
            "order_status": "ALL"
        }, access_token, shop_id)
        orders = resp.get("response", {}).get("order_list", []) or []
        all_orders.extend(orders)
        more = resp.get("response", {}).get("more", False)
        if not more or len(orders) == 0:
            break
        offset += len(orders)
        if offset >= 500:
            break

    # 4. PEDIDOS 31-60 DIAS (para comparaÃ§Ã£o de crescimento)
    print(f"  Total 30 dias: {len(all_orders)} pedidos")
    prev_orders = []
    offset = 0
    while True:
        resp = api("/api/v2/order/get_order_list", {
            "time_range_field": "create_time",
            "time_from": d60, "time_to": d30,
            "page_size": 100, "cursor": str(offset),
            "order_status": "ALL"
        }, access_token, shop_id)
        orders = resp.get("response", {}).get("order_list", []) or []
        prev_orders.extend(orders)
        more = resp.get("response", {}).get("more", False)
        if not more or len(orders) == 0:
            break
        offset += len(orders)
        if offset >= 500:
            break
    print(f"  Total 31-60 dias: {len(prev_orders)} pedidos")

    if len(prev_orders) > 0:
        crescimento = ((len(all_orders) - len(prev_orders)) / len(prev_orders)) * 100
        print(f"  ð Crescimento 30 dias: {crescimento:+.1f}%")
    else:
        print("  ð Crescimento: sem dados do perÃ­odo anterior")

    # 5. ANÃLISE DETALHADA DOS PEDIDOS 30 DIAS
    if all_orders:
        sns = [o["order_sn"] for o in all_orders[:50]]
        detail_resp = api_post("/api/v2/order/get_order_detail", {
            "order_sn_list": sns,
            "response_optional_fields": ["item_list", "actual_price", "order_status"]
        }, access_token, shop_id)

        orders_detail = detail_resp.get("response", {}).get("order_list", []) or []

        total_revenue = 0
        cancelados = 0
        nao_pagos = 0
        nao_enviados = 0
        pagos = 0
        status_count = {}

        for o in orders_detail:
            status = o.get("order_status", "UNKNOWN")
            status_count[status] = status_count.get(status, 0) + 1
            price = float(o.get("actual_price", 0) or 0)

            if status in ("COMPLETED", "SHIPPED", "IN_CANCEL", "TO_SHIP", "TO_RECEIVE"):
                total_revenue += price
                pagos += 1
            elif status == "UNPAID":
                nao_pagos += 1
                cancelados += 1
            elif status in ("CANCELLED",):
                cancelados += 1
                cancel_reason = o.get("cancel_reason", "")
                if "seller" in cancel_reason.lower():
                    nao_enviados += 1

        total = len(orders_detail)
        print(f"\n--- 4. MÃTRICAS DETALHADAS (primeiros 50 pedidos) ---")
        print(f"  Faturamento: R${total_revenue:.2f}")
        print(f"  Pedidos pagos/ativos: {pagos}")
        print(f"  Cancelados: {cancelados} ({cancelados/total*100:.1f}% de {total})")
        print(f"  NÃ£o pagos: {nao_pagos}")
        print(f"  NÃ£o enviados pelo vendedor: {nao_enviados}")
        print(f"  Status breakdown: {json.dumps(status_count)}")
        if pagos > 0:
            ticket = total_revenue / pagos
            print(f"  Ticket mÃ©dio: R${ticket:.2f}")

    # 6. ITENS DA LOJA (produtos)
    print("\n--- 5. PRODUTOS ---")
    items_resp = api("/api/v2/product/get_item_list", {
        "offset": 0, "page_size": 50,
        "item_status": "NORMAL"
    }, access_token, shop_id)

    if "response" in items_resp:
        items = items_resp["response"].get("item", []) or []
        total_items = items_resp["response"].get("total_count", len(items))
        print(f"  Produtos ativos: {total_items}")

        # Check for items with no stock or issues
        items_detail_ids = [i["item_id"] for i in items[:20]]
        if items_detail_ids:
            detail = api_post("/api/v2/product/get_item_base_info", {
                "item_id_list": items_detail_ids,
                "need_complaint_policy": False
            }, access_token, shop_id)

            item_list = detail.get("response", {}).get("item_list", []) or []
            sem_estoque = sum(1 for i in item_list if i.get("stock", {}).get("current_stock", 1) == 0)
            precos = [float(i.get("price_info", [{}])[0].get("current_price", 0))
                     for i in item_list if i.get("price_info")]
            if precos:
                print(f"  Produtos sem estoque (amostra 20): {sem_estoque}")
                print(f"  PreÃ§o mÃ©dio produtos: R${sum(precos)/len(precos)/100000:.2f}")
                print(f"  Menor preÃ§o: R${min(precos)/100000:.2f}")
                print(f"  Maior preÃ§o: R${max(precos)/100000:.2f}")
    else:
        print(f"  {json.dumps(items_resp)[:200]}")

    # 7. REVIEWS RECENTES
    print("\n--- 6. AVALIAÃÃES RECENTES ---")
    reviews = api("/api/v2/product/get_comment", {
        "page_size": 20, "cursor": "0",
        "rating_filter": 1  # 1 = all, 2 = with comment
    }, access_token, shop_id)

    if "response" in reviews:
        r = reviews["response"]
        comment_list = r.get("comment_list", []) or []
        if comment_list:
            stars = [c.get("rating_star", 0) for c in comment_list]
            avg_stars = sum(stars) / len(stars) if stars else 0
            bad_reviews = [c for c in comment_list if c.get("rating_star", 5) <= 2]
            print(f"  AvaliaÃ§Ãµes recentes: {len(comment_list)}")
            print(f"  MÃ©dia estrelas (amostra): {avg_stars:.1f}")
            print(f"  AvaliaÃ§Ãµes 1-2 estrelas: {len(bad_reviews)}")
            for br in bad_reviews[:3]:
                print(f"  â­{br.get('rating_star')} - {br.get('comment', '')[:100]}")
        else:
            print("  Sem avaliaÃ§Ãµes recentes")
    else:
        print(f"  {json.dumps(reviews)[:200]}")

    print(f"\n{'='*60}")
    print("ANÃLISE CONCLUÃDA")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
