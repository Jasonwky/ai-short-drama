# -*- coding: utf-8 -*-
"""控制台应用层:统一的拒绝 / 出错页面。"""
def test_malformed_form_fields_get_a_chinese_400_not_a_422(client, sample):
    """表单数字填错(浏览器端 type=number 拦不住的,比如手改请求):和其他拒绝原因一样给人话的 400 页面。"""
    r = client.post(f"/segment/{sample['seg']}/frame", data={"seed": "abc"})
    assert r.status_code == 400
    assert "表单填得不对" in r.text and "seed" in r.text and "整数" in r.text and "abc" in r.text
    r = client.post(f"/segment/{sample['seg']}/frame", data={})
    assert r.status_code == 400 and "seed 没填" in r.text
