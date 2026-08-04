# 上海旅游景点研究 — 图片处理全链条还原（2026-08-04 run）

- 研究 run id: `d4391a61-3afa-4f0a-a34a-7d9dc61bcb04`
- 查询主题: 上海旅游景点
- 数据来源: ldr-local 容器 stdout（Aug 4 15:35:17 → 16:39:07）
- 本次 run 时长: 3829.8 s（overall stage）

## 摘要

- 抓源页阶段 `FETCHED_IMG` 事件：**238 条**
- 唯一图片 URL：**235 张**（按 img_url 去重）
- 唯一源网页（src_url）：**9 个**
- langgraph 预填充阶段 `LANGGRAPH_FILLED_IMG`：0 条（未进入 langgraph；本次走 deferred 路径）
- 候选库 `ELIGIBLE_BANK total=0` → `INSERT placements=0` → `PERSIST chosen=0` → `END status=empty`
- **Markdown 实际采纳为插图的图片：0 张**

> 与 Aug 2-3 那次（run 326cee3c…）对比：上次 1855 张 fill → 1 张通过 gate → Markdown 1 张插图；
> 本次 238 张 FETCHED_IMG（仅 stage 1）→ 0 张进 ELIGIBLE_BANK → 0 张采纳。

## 一、按源网页分组的图片清单

每行：源网页 URL → 该源解析出的图片数。

| # | 源网页 (source URL) | 解析图片数 | 唯一 img_url |
|---|----------------------|-----------:|-------------:|
| 1 | https://baike.baidu.com/item/%E5%A4%96%E6%BB%A9/40416 | 96 | 96 |
| 2 | https://en.wikipedia.org/wiki/Shanghai | 58 | 58 |
| 3 | https://zh.wikipedia.org/wiki/%E5%A4%96%E6%BB%A9 | 43 | 43 |
| 4 | https://www.yugarden.com.cn/page/articleView/index.html | 14 | 11 |
| 5 | https://zh.wikipedia.org/zh-hans/%E8%B1%AB%E5%9C%92%E5%95%86%E5%9F%8E | 10 | 10 |
| 6 | https://en.wikipedia.org/wiki/Oriental_Pearl_Tower | 8 | 8 |
| 7 | https://www.thepaper.cn/newsDetail_forward_24998814 | 5 | 5 |
| 8 | https://en.wikipedia.org/wiki/List_of_tourist_attractions_in_Shanghai | 2 | 2 |
| 9 | https://en.wikipedia.org/wiki/Tianzifang | 2 | 2 |

## 二、每张图片的明细（按源分组）

每张图 5 个字段：source 网页 URL、图片 alt 文字、图片 URL（img_url）、是否被 Markdown 采纳、备注。

> **采纳状态**：本次 run 的 `ELIGIBLE_BANK total=0`，故所有图片均**未被 Markdown 采纳**。

### 源 `https://baike.baidu.com/item/%E5%A4%96%E6%BB%A9/40416`（共 96 张）

| 序号 | 图片 alt 文字 | 图片 URL (img_url) | 是否采纳 | 备注 |
|----:|---------------|---------------------|----------|------|
| 1 | `new` | https://baikebcs.bdimg.com/baike-react/common/new.png | ❌ 否 | alt 有内容 |
| 2 | `订阅更新` | https://baikebcs.bdimg.com/front-end/swanapp-baike/subscribe/unsubscribe-blue.png | ❌ 否 | alt 有内容 |
| 3 | `` | https://bkimg.cdn.bcebos.com/pic/08f790529822720ef399e33177cb0a46f31faba2?x-bce-process=image/format,f_auto/resize,m_lfit,limit_1,h_285 | ❌ 否 | alt 为空 |
| 4 | `` | https://bkimg.cdn.bcebos.com/pic/10dfa9ec8a136327e5796ffe9d8fa0ec09fac763?x-bce-process=image/format,f_auto/resize,m_lfit,limit_1,h_333 | ❌ 否 | alt 为空 |
| 5 | `` | https://bkimg.cdn.bcebos.com/pic/14ce36d3d539b60031617e7ce550352ac75cb749?x-bce-process=image/format,f_auto/resize,m_lfit,limit_1,h_294 | ❌ 否 | alt 为空 |
| 6 | `` | https://bkimg.cdn.bcebos.com/pic/1a94b36e364a869180cb4a7f?x-bce-process=image/format,f_auto/resize,m_lfit,limit_1,w_440 | ❌ 否 | alt 为空 |
| 7 | `` | https://bkimg.cdn.bcebos.com/pic/1ad5ad6eddc451da0c3b0620bafd5266d116322f?x-bce-process=image/format,f_auto/resize,m_lfit,limit_1,w_440 | ❌ 否 | alt 为空 |
| 8 | `` | https://bkimg.cdn.bcebos.com/pic/279759ee3d6d55fb38fcdceb61224f4a21a4ddc1?x-bce-process=image/format,f_auto/resize,m_lfit,limit_1,h_280 | ❌ 否 | alt 为空 |
| 9 | `` | https://bkimg.cdn.bcebos.com/pic/3bf33a87e950352ac65c5d55aa18ecf2b21193131d60?x-bce-process=image/format,f_auto/resize,m_lfit,limit_1,w_293 | ❌ 否 | alt 为空 |
| 10 | `` | https://bkimg.cdn.bcebos.com/pic/5366d0160924ab181cd99cfc39fae6cd7a890b29?x-bce-process=image/format,f_auto/resize,m_lfit,limit_1,w_440 | ❌ 否 | alt 为空 |
| 11 | `` | https://bkimg.cdn.bcebos.com/pic/5882b2b7d0a20cf431ad94838f525c36acaf2edd3361?x-bce-process=image/format,f_auto/resize,m_lfit,limit_1,w_440 | ❌ 否 | alt 为空 |
| 12 | `` | https://bkimg.cdn.bcebos.com/pic/8644ebf81a4c510f07f9b0b36859252dd42aa55e?x-bce-process=image/format,f_auto/resize,m_lfit,limit_1,w_440 | ❌ 否 | alt 为空 |
| 13 | `` | https://bkimg.cdn.bcebos.com/pic/aa64034f78f0f736015044ca0655b319eac4134a?x-bce-process=image/format,f_auto/resize,m_lfit,limit_1,w_440 | ❌ 否 | alt 为空 |
| 14 | `` | https://bkimg.cdn.bcebos.com/pic/bba1cd11728b4710d6498407cfcec3fdfd032392?x-bce-process=image/format,f_auto/resize,m_lfit,limit_1,h_281 | ❌ 否 | alt 为空 |
| 15 | `` | https://bkimg.cdn.bcebos.com/pic/d53f8794a4c27d1ed039cdea16d5ad6eddc43884?x-bce-process=image/format,f_auto/resize,m_lfit,limit_1,h_293 | ❌ 否 | alt 为空 |
| 16 | `` | https://bkimg.cdn.bcebos.com/pic/dc854fda7de6f1f5b6fd48f9?x-bce-process=image/format,f_auto/resize,m_lfit,limit_1,h_291 | ❌ 否 | alt 为空 |
| 17 | `` | https://bkimg.cdn.bcebos.com/pic/fc1f4134970a304eecc27436ddc8a786c8175c1e?x-bce-process=image/format,f_auto/resize,m_lfit,limit_1,w_440 | ❌ 否 | alt 为空 |
| 18 | `外滩 愛東的賢/摄` | https://bkimg.cdn.bcebos.com/smart/00e93901213fb80e7bec0126268a382eb9389a503eb5-bkimg-process,v_1,rw_6247,rh_4165,maxl_755?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 19 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/00e93901213fb80e7bec015a268a382eb9389a503eb1-bkimg-process,v_1,rw_1440,rh_1080,maxl_378?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 20 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/0823dd54564e9258d109101474dbc658ccbf6d81e08d-bkimg-process,v_1,rw_3240,rh_2160,maxl_426?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 21 | `` | https://bkimg.cdn.bcebos.com/smart/0bd162d9f2d3572c11dfe46f4b4a742762d0f7036574-bkimg-process,v_1,rw_336,rh_252,maxl_672?x-bce-process=image/format,f_auto | ❌ 否 | alt 为空 |
| 22 | `外滩 愛東的賢/摄` | https://bkimg.cdn.bcebos.com/smart/203fb80e7bec54e736d178efa9638c504fc2d4623cb5-bkimg-process,v_1,rw_3,rh_4,maxl_504?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 23 | `外滩` | https://bkimg.cdn.bcebos.com/smart/241f95cad1c8a786c917398b3c50de3d70cf3ac7faa7-bkimg-process,v_1,rw_1242,rh_828,maxl_426?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 24 | `外滩 愛東的賢/摄` | https://bkimg.cdn.bcebos.com/smart/2cf5e0fe9925bc315c6045694e849ab1cb134854dab5-bkimg-process,v_1,rw_6720,rh_4480,maxl_756?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 25 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/2cf5e0fe9925bc315c60ad62b6869ab1cb134854da8d-bkimg-process,v_1,rw_16,rh_9,maxl_504?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 26 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/2e2eb9389b504fc2d562d988f586f01190ef77c6c7b1-bkimg-process,v_1,rw_1231,rh_923,maxl_378?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 27 | `外滩 愛東的賢/摄` | https://bkimg.cdn.bcebos.com/smart/2e2eb9389b504fc2d562d9f4f586f01190ef77c6c7b5-bkimg-process,v_1,rw_6720,rh_4480,maxl_756?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 28 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/2fdda3cc7cd98d1001e968bc3164af0e7bec55e73ab1-bkimg-process,v_1,rw_16,rh_9,maxl_504?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 29 | `外滩` | https://bkimg.cdn.bcebos.com/smart/314e251f95cad1c8a7868ca224677009c93d71cffba7-bkimg-process,v_1,rw_3,rh_4,maxl_284?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 30 | `外滩` | https://bkimg.cdn.bcebos.com/smart/32fa828ba61ea8d3fd1f7f81cc53274e251f94caf2a7-bkimg-process,v_1,rw_3,rh_4,maxl_284?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 31 | `外滩` | https://bkimg.cdn.bcebos.com/smart/3792cb39b58a79b33b87ce6f-bkimg-process,v_1,rw_500,rh_404,maxl_351?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 32 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/37d12f2eb9389b504fc28edf956ef2dde71191efc0b1-bkimg-process,v_1,rw_1200,rh_901,maxl_378?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 33 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/3801213fb80e7bec54e7481bc777ae389b504ec23d8d-bkimg-process,v_1,rw_3240,rh_2160,maxl_426?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 34 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/3b292df5e0fe9925bc31c4aadcf149df8db1ca13db8d-bkimg-process,v_1,rw_4912,rh_3264,maxl_427?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 35 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/3b87e950352ac65cb033e796f7f2b21193138a25-bkimg-process,v_1,rw_6000,rh_4000,maxl_426?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 36 | `` | https://bkimg.cdn.bcebos.com/smart/3bf33a87e950352a37ea76a15b43fbf2b3118b94-bkimg-process,v_1,rw_378,rh_252,maxl_757?x-bce-process=image/format,f_auto | ❌ 否 | alt 为空 |
| 37 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/48540923dd54564e925873c35b878b82d158cdbfe18d-bkimg-process,v_1,rw_3240,rh_2160,maxl_426?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 38 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/4ec2d5628535e5dde711cf52669db0efce1b9c16c4b1-bkimg-process,v_1,rw_1440,rh_1080,maxl_378?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 39 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/574e9258d109b3de9c82749224e67b81800a18d8e68d-bkimg-process,v_1,rw_3240,rh_2160,maxl_426?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 40 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/5d6034a85edf8db1cb13d49ee17aca54564e9358de8d-bkimg-process,v_1,rw_3,rh_4,maxl_284?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 41 | `` | https://bkimg.cdn.bcebos.com/smart/647912d78491d79da044df57-bkimg-process,v_1,rw_360,rh_252,maxl_721?x-bce-process=image/format,f_auto | ❌ 否 | alt 为空 |
| 42 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/77c6a7efce1b9d1633f71b84ffdeb48f8c546427-bkimg-process,v_1,rw_16,rh_9,maxl_504?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 43 | `外滩 愛東的賢/摄` | https://bkimg.cdn.bcebos.com/smart/77c6a7efce1b9d16fdfaca90e385a38f8c5495eeceb5-bkimg-process,v_1,rw_3240,rh_2160,maxl_756?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 44 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/77c6a7efce1b9d16fdfacaece385a38f8c5495eeceb1-bkimg-process,v_1,rw_770,rh_1026,maxl_284?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 45 | `外滩 愛東的賢/摄` | https://bkimg.cdn.bcebos.com/smart/7aec54e736d12f2eb938c2915f99c2628535e4ddc2b5-bkimg-process,v_1,rw_6436,rh_4290,maxl_756?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 46 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/7c1ed21b0ef41bd5bb976e115dda81cb38db3d0a-bkimg-process,v_1,rw_6000,rh_4000,maxl_426?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 47 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/7dd98d1001e93901213febb36bb743e736d12e2e38b1-bkimg-process,v_1,rw_1440,rh_1080,maxl_378?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 48 | `外滩 愛東的賢/摄` | https://bkimg.cdn.bcebos.com/smart/7dd98d1001e93901213febcf6bb743e736d12e2e38b5-bkimg-process,v_1,rw_4759,rh_3173,maxl_755?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 49 | `外滩` | https://bkimg.cdn.bcebos.com/smart/838ba61ea8d3fd1f4134a8bf6b17321f95cad0c8f1a7-bkimg-process,v_1,rw_6000,rh_4000,maxl_426?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 50 | `外滩 愛東的賢/摄` | https://bkimg.cdn.bcebos.com/smart/8435e5dde71190ef76c6c62ede408a16fdfaae51cab5-bkimg-process,v_1,rw_6140,rh_4094,maxl_755?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 51 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/8435e5dde71190ef76c6c652de408a16fdfaae51cab1-bkimg-process,v_1,rw_770,rh_1026,maxl_284?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 52 | `外滩 愛東的賢/摄` | https://bkimg.cdn.bcebos.com/smart/8c1001e93901213fb80e2f2d44bc21d12f2eb8383fb5-bkimg-process,v_1,rw_5941,rh_3961,maxl_755?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 53 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/8c1001e93901213fb80e2f5144bc21d12f2eb8383fb1-bkimg-process,v_1,rw_1440,rh_1080,maxl_378?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 54 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/8d5494eef01f3a292df57534717cab315c6035a8d68d-bkimg-process,v_1,rw_16,rh_9,maxl_504?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 55 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/8d5494eef01f3a292df58d43897eab315c6035a8d6b1-bkimg-process,v_1,rw_770,rh_1026,maxl_284?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 56 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/8d5494eef01f3a29fba8542b9525bc315d607cf7-bkimg-process,v_1,rw_7908,rh_6000,maxl_374?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 57 | `外滩 愛東的賢/摄` | https://bkimg.cdn.bcebos.com/smart/91ef76c6a7efce1b9d16993bbf0ae4deb48f8d54cfb5-bkimg-process,v_1,rw_3,rh_4,maxl_504?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 58 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/9358d109b3de9c82d1586a7584d8970a19d8bd3ee58d-bkimg-process,v_1,rw_3240,rh_2160,maxl_426?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 59 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/95eef01f3a292df5e0fe0fef54684b6034a85fdfd58d-bkimg-process,v_1,rw_4912,rh_3264,maxl_427?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 60 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/95eef01f3a292df5e0fef798ac6a4b6034a85fdfd5b1-bkimg-process,v_1,rw_1196,rh_897,maxl_378?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 61 | `外滩` | https://bkimg.cdn.bcebos.com/smart/960a304e251f95cad1c8e333924e683e6709c83df4a7-bkimg-process,v_1,rw_3,rh_4,maxl_284?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 62 | `` | https://bkimg.cdn.bcebos.com/smart/9825bc315c6034a85edfb2d18f495e540923dd54d813-bkimg-process,v_1,rw_189,rh_252,maxl_504?x-bce-process=image/format,f_auto | ❌ 否 | alt 为空 |
| 63 | `上海外滩` | https://bkimg.cdn.bcebos.com/smart/9922720e0cf3d7ca494e03e8f41fbe096a63a9f7-bkimg-process,v_1,rw_16,rh_9,maxl_504?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 64 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/9a504fc2d5628535e5ddb9ac80b461c6a7efcf1bc5b1-bkimg-process,v_1,rw_1440,rh_1080,maxl_378?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 65 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/9c16fdfaaf51f3deb48f1c9e7cb7e71f3a292cf5d38d-bkimg-process,v_1,rw_3264,rh_2305,maxl_402?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 66 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/9c16fdfaaf51f3deb48fe4e984b5e71f3a292cf5d3b1-bkimg-process,v_1,rw_3,rh_4,maxl_284?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 67 | `外滩` | https://bkimg.cdn.bcebos.com/smart/9f510fb30f2442a7a41cad4fd743ad4bd01302cd-bkimg-process,v_1,rw_1600,rh_1200,maxl_378?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 68 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/a2cc7cd98d1001e939017382a8556cec54e737d139b1-bkimg-process,v_1,rw_1296,rh_972,maxl_378?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 69 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/a6efce1b9d16fdfaaf519563a4d49b5494eef11fcdb1-bkimg-process,v_1,rw_16,rh_9,maxl_504?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 70 | `外滩` | https://bkimg.cdn.bcebos.com/smart/a71ea8d3fd1f4134970a70fb7e4682cad1c8a686f0a7-bkimg-process,v_1,rw_5999,rh_3999,maxl_426?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 71 | `外滩` | https://bkimg.cdn.bcebos.com/smart/a75fb6d3a6800274960a1644-bkimg-process,v_1,rw_500,rh_342,maxl_415?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 72 | `外滩` | https://bkimg.cdn.bcebos.com/smart/a9d3fd1f4134970a304e64aace93c6c8a786c817f7a7-bkimg-process,v_1,rw_5923,rh_3949,maxl_425?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 73 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/ac345982b2b7d0a2bcff8578c7ef76094b369a25-bkimg-process,v_1,rw_6000,rh_4000,maxl_426?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 74 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/ae51f3deb48f8c5494ee62d5d2703af5e0fe9825d18d-bkimg-process,v_1,rw_4912,rh_2841,maxl_491?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 75 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/ae51f3deb48f8c5494ee9aa22a723af5e0fe9825d1b1-bkimg-process,v_1,rw_1329,rh_997,maxl_378?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 76 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/b03533fa828ba61e5a344eca4d34970a314e5979-bkimg-process,v_1,rw_6000,rh_4000,maxl_426?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 77 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/b2de9c82d158ccbf6c8128c0f181ab3eb13532faeb8d-bkimg-process,v_1,rw_3240,rh_2160,maxl_426?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 78 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/b58f8c5494eef01f3a29b93f08a78e25bc315d60d78d-bkimg-process,v_1,rw_4912,rh_3264,maxl_427?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 79 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/b90e7bec54e736d12f2ee1858b0b58c2d5628435c3b1-bkimg-process,v_1,rw_1185,rh_888,maxl_378?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 80 | `外滩 愛東的賢/摄` | https://bkimg.cdn.bcebos.com/smart/b90e7bec54e736d12f2ee1f98b0b58c2d5628435c3b5-bkimg-process,v_1,rw_6415,rh_3609,maxl_895?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 81 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/bd315c6034a85edf8db157d9a10d1e23dd54574edf8d-bkimg-process,v_1,rw_4912,rh_3264,maxl_427?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 82 | `外滩全景` | https://bkimg.cdn.bcebos.com/smart/bf096b63f6246b605cd7743beef81a4c500fa2a0-bkimg-process,v_1,rw_16,rh_9,maxl_504?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 83 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/cdbf6c81800a19d8bc3e1affdba3958ba61ea9d3e88d-bkimg-process,v_1,rw_3240,rh_2160,maxl_426?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 84 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/cf1b9d16fdfaaf51f3ded3329c0f83eef01f3b29ccb1-bkimg-process,v_1,rw_613,rh_816,maxl_284?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 85 | `` | https://bkimg.cdn.bcebos.com/smart/d000baa1cd11728b8a8954eec4fcc3cec2fd2c52-bkimg-process,v_1,rw_378,rh_252,maxl_756?x-bce-process=image/format,f_auto | ❌ 否 | alt 为空 |
| 86 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/d058ccbf6c81800a19d816f4596c24fa828ba71ee98d-bkimg-process,v_1,rw_3240,rh_2160,maxl_426?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 87 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/d4628535e5dde71190ef167bb7b4d91b9d16fcfacbb1-bkimg-process,v_1,rw_1105,rh_831,maxl_377?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 88 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/dc54564e9258d109b3de38483901dbbf6c81810ae78d-bkimg-process,v_1,rw_3240,rh_2160,maxl_426?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 89 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/e4dde71190ef76c6a7efaca68d4deafaaf51f2dec9b1-bkimg-process,v_1,rw_689,rh_918,maxl_284?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 90 | `外滩 愛東的賢/摄` | https://bkimg.cdn.bcebos.com/smart/e4dde71190ef76c6a7efacda8d4deafaaf51f2dec9b5-bkimg-process,v_1,rw_6720,rh_4480,maxl_756?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 91 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/e61190ef76c6a7efce1bfeabeda1b851f3deb58fc8b1-bkimg-process,v_1,rw_1381,rh_1035,maxl_378?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 92 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/f11f3a292df5e0fe9925d38c4c3b23a85edf8cb1d4b1-bkimg-process,v_1,rw_1440,rh_1080,maxl_378?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 93 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/f2deb48f8c5494eef01f51943daef7fe9925bd31d0b1-bkimg-process,v_1,rw_1329,rh_997,maxl_378?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 94 | `外滩` | https://bkimg.cdn.bcebos.com/smart/fc1f4134970a304e251fd77f8a91b086c9177e3ef6a7-bkimg-process,v_1,rw_5957,rh_3971,maxl_426?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 95 | `外滩夜景` | https://bkimg.cdn.bcebos.com/smart/fcfaaf51f3deb48f8c54052418462d292df5e1fed28d-bkimg-process,v_1,rw_3,rh_4,maxl_284?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |
| 96 | `外滩 凯西娘娘/摄` | https://bkimg.cdn.bcebos.com/smart/fcfaaf51f3deb48f8c54fd53e0442d292df5e1fed2b1-bkimg-process,v_1,rw_810,rh_608,maxl_378?x-bce-process=image/format,f_auto | ❌ 否 | alt 有内容 |

### 源 `https://en.wikipedia.org/wiki/Shanghai`（共 58 张）

| 序号 | 图片 alt 文字 | 图片 URL (img_url) | 是否采纳 | 备注 |
|----:|---------------|---------------------|----------|------|
| 1 | `Map` | https://maps.wikimedia.org/img/osm-intl,7,31.22,122.1,300x200.png?lang=en&domain=en.wikipedia.org&title=Shanghai&revid=1367552812&groups=_32a600d5524a3c707239761dbb36fdbb9d0494e9&parser=parsoid | ❌ 否 | alt 有内容 |
| 2 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/0/03/1967-12_1967%E5%B9%B4_%E4%B8%8A%E6%B5%B7%E5%B8%82%E5%8D%97%E4%BA%AC%E8%B7%AF.jpg/250px-1967-12_1967%E5%B9%B4_%E4%B8%8A%E6%B5%B7%E5%B8%82%E5%8D%97%E4%BA%AC%E8%B7%AF.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 3 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/0/05/%E6%9D%BE%E6%B1%9F%E6%9C%89%E8%BD%A8%E7%94%B5%E8%BD%A6_Songjiang_Tram.jpg/120px-%E6%9D%BE%E6%B1%9F%E6%9C%89%E8%BD%A8%E7%94%B5%E8%BD%A6_Songjiang_Tram.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 4 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/1/17/Shanghai_Metro_09A04.jpg/250px-Shanghai_Metro_09A04.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 5 | `Blue hour view over Huangpu` | https://upload.wikimedia.org/wikipedia/commons/thumb/1/18/Shanghai_-13_-_Blue_hour_over_Huangpu_%2832354797618%29.jpg/250px-Shanghai_-13_-_Blue_hour_over_Huangpu_%2832354797618%29.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 有内容 |
| 6 | `View of skyscrapers in Lujiazui from The Bund` | https://upload.wikimedia.org/wikipedia/commons/thumb/2/20/20045-Shanghai-Pano_%28cropped%29.jpg/250px-20045-Shanghai-Pano_%28cropped%29.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 有内容 |
| 7 | `Night view of skyscrapers in Lujiazui from The Bund` | https://upload.wikimedia.org/wikipedia/commons/thumb/3/32/Pudong_Shanghai_November_2017_HDR_panorama.jpg/250px-Pudong_Shanghai_November_2017_HDR_panorama.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 有内容 |
| 8 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/3/36/Russian_Consulate_General_in_Shanghai.jpg/250px-Russian_Consulate_General_in_Shanghai.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 9 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/3/37/Shanghai_haze_in_Huangpu_Distract_20131206.jpg/250px-Shanghai_haze_in_Huangpu_Distract_20131206.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 10 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/3/3b/Mei_Lanfang_performing_at_Tianchan_Theatre.jpg/250px-Mei_Lanfang_performing_at_Tianchan_Theatre.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 11 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/3/3b/Shanghai_Railway_Station_4.jpg/250px-Shanghai_Railway_Station_4.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 12 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/3/3e/Yangshan-Port-Balanced.jpg/250px-Yangshan-Port-Balanced.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 13 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/4/43/20191027_Xuhui_Campus_of_SJTU_04.jpg/250px-20191027_Xuhui_Campus_of_SJTU_04.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 14 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/4/45/Shanghai_-_Yu_Garden_-_0035.jpg/250px-Shanghai_-_Yu_Garden_-_0035.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 15 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/4/46/Shanghai1937city_zhabei_fire.jpg/250px-Shanghai1937city_zhabei_fire.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 16 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/Shanghai_of_Today%2C_Plate_23_-_Shanghai_Racecourse%2C_stands_and_administration.jpg/250px-Shanghai_of_Today%2C_Plate_23_-_Shanghai_Racecourse%2C_stands_and_administration.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 17 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/4/4c/Huangpu_Park_20124-Shanghai_%2832208802494%29.jpg/330px-Huangpu_Park_20124-Shanghai_%2832208802494%29.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 18 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/4/4e/F-22P_PNS_Zulfiquar.JPG/250px-F-22P_PNS_Zulfiquar.JPG?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 19 | `Aerial view of Hongkou District` | https://upload.wikimedia.org/wikipedia/commons/thumb/4/4f/Aerial_view_of_Hongkou_District.jpg/250px-Aerial_view_of_Hongkou_District.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 有内容 |
| 20 | `The Shanghai Exhibition Center, an example of Stalinist architecture` | https://upload.wikimedia.org/wikipedia/commons/thumb/5/5b/The_Sino-Soviet_Friendship_Mansion.JPG/250px-The_Sino-Soviet_Friendship_Mansion.JPG?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 有内容 |
| 21 | `Yangtze River Delta` | https://upload.wikimedia.org/wikipedia/commons/thumb/5/5e/Yangtze_River_Delta.gif/120px-Yangtze_River_Delta.gif?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 有内容 |
| 22 | `Location of Shanghai Municipality in China` | https://upload.wikimedia.org/wikipedia/commons/thumb/6/67/Shanghai_in_China_%28%2Ball_claims_hatched%29.svg/250px-Shanghai_in_China_%28%2Ball_claims_hatched%29.svg.png?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 有内容 |
| 23 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/6/6d/Tsonga_Potro_2008_Tennis_Masters.jpg/250px-Tsonga_Potro_2008_Tennis_Masters.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 24 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/8/83/Bund_at_night_Shanghai.jpg/250px-Bund_at_night_Shanghai.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 25 | `Blue hour view of the Bund from the Shanghai World Financial Center` | https://upload.wikimedia.org/wikipedia/commons/thumb/8/86/Blue_hour_view_of_the_Bund_from_the_Shanghai_World_Financial_Center_dllu.jpg/250px-Blue_hour_view_of_the_Bund_from_the_Shanghai_World_Financial_Center_dllu.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 有内容 |
| 26 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/8/86/Old_City_of_Shanghai_will_walls_and_seafront.jpg/250px-Old_City_of_Shanghai_will_walls_and_seafront.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 27 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/8/89/Xiao_Long_Bao_at_Nanxiang_Mantou_Dian_1.jpg/250px-Xiao_Long_Bao_at_Nanxiang_Mantou_Dian_1.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 28 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/8/8a/Longhua_Temple_in_Shanghai_%28Panorama%29.jpg/250px-Longhua_Temple_in_Shanghai_%28Panorama%29.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 29 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/8/8b/%E5%A4%A7%E4%B8%8A%E6%B5%B7%E9%83%BD%E5%B8%82%E5%9C%88.jpg/250px-%E5%A4%A7%E4%B8%8A%E6%B5%B7%E9%83%BD%E5%B8%82%E5%9C%88.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 30 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/a/a1/Shanghai_%28Chinese_characters%29.svg/250px-Shanghai_%28Chinese_characters%29.svg.png?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 31 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/a/a1/Shanghai_Bund_seen_from_the_French_Concession.jpg/250px-Shanghai_Bund_seen_from_the_French_Concession.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 32 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/a/a8/%E6%98%A5%E5%85%B0%E9%9B%86%E5%9B%A2%C2%B7%E7%8A%B9%E5%A4%AA%E4%BA%BA%E6%80%BB%E4%BC%9A%C2%B7%E4%B8%8A%E6%B5%B7.jpg/250px-%E6%98%A5%E5%85%B0%E9%9B%86%E5%9B%A2%C2%B7%E7%8A%B9%E5%A4%AA%E4%BA%BA%E6%80%BB%E4%BC%9A%C2%B7%E4%B8%8A%E6%B5%B7.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 33 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Renxiong_wan04s.jpg/250px-Renxiong_wan04s.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 34 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/b/b6/Shanghai_1884.jpg/250px-Shanghai_1884.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 35 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/b/bd/CADAL03020496_%E6%96%B0%E4%B8%8A%E6%B5%B7.djvu/page1-250px-CADAL03020496_%E6%96%B0%E4%B8%8A%E6%B5%B7.djvu.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 36 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/c/c0/2024-Apr_Shanghai_East_Nanjing_Road_morning_01.jpg/120px-2024-Apr_Shanghai_East_Nanjing_Road_morning_01.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 37 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/c/c2/Shanghai_Stock_Exchange_2.jpg/250px-Shanghai_Stock_Exchange_2.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 38 | `The Shanghai Museum` | https://upload.wikimedia.org/wikipedia/commons/thumb/c/c5/%C2%B7%CB%99%C2%B7ChinaUli2010%C2%B7.%C2%B7_Shanghai_-_panoramio_%28231%29.jpg/250px-%C2%B7%CB%99%C2%B7ChinaUli2010%C2%B7.%C2%B7_Shanghai_-_panoramio_%28231%29.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 有内容 |
| 39 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/c/c7/Jing%27an_Temple_Shanghai_6.jpg/250px-Jing%27an_Temple_Shanghai_6.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 40 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/c/c8/%E4%B8%8A%E6%B5%B7%E5%B1%95%E8%A7%88%E4%B8%AD%E5%BF%83%C2%B7%E4%B8%8A%E6%B5%B7.jpg/250px-%E4%B8%8A%E6%B5%B7%E5%B1%95%E8%A7%88%E4%B8%AD%E5%BF%83%C2%B7%E4%B8%8A%E6%B5%B7.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 41 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/c/ca/Yan%27an_East_Road_Interchange%2C_Shanghai%2C_China_%28Unsplash%29.jpg/250px-Yan%27an_East_Road_Interchange%2C_Shanghai%2C_China_%28Unsplash%29.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 42 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/c/cb/Shanghai_disneyland_castle.jpg/250px-Shanghai_disneyland_castle.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 43 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/c/cd/Shanghai_F1_Circui_01.jpg/250px-Shanghai_F1_Circui_01.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 44 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/d/d1/A_maglev_train_coming_out%2C_Pudong_International_Airport%2C_Shanghai.jpg/250px-A_maglev_train_coming_out%2C_Pudong_International_Airport%2C_Shanghai.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 45 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/d/d2/Sprawling_Shanghai_2016-07-20.jpg/250px-Sprawling_Shanghai_2016-07-20.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 46 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/d/d4/Shanghai%2C_China_%28Unsplash_8T9p4FDu590%29.jpg/250px-Shanghai%2C_China_%28Unsplash_8T9p4FDu590%29.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 47 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/d/d9/Fudan-guanghualou2.jpg/250px-Fudan-guanghualou2.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 48 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/A_residual_waste_truck_and_a_household_food_waste_truck_on_Zhonghua_Road%2C_Shanghai.jpg/250px-A_residual_waste_truck_and_a_household_food_waste_truck_on_Zhonghua_Road%2C_Shanghai.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 49 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/e/e3/Shanghai_Pudong_International_Airport_Interior.jpg/250px-Shanghai_Pudong_International_Airport_Interior.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 50 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/e/e6/China_Art_Museum_1.jpg/250px-China_Art_Museum_1.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 51 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/e/e7/Sunwin_SWB6109BEV69G_%28iEV10%29_S0S-0224_and_SWB5129BEV77G_%28iEV12T%29_S5A-0069_at_Zhongshan_Rd_%28S-1%29_Xizang_Rd_S.jpg/250px-Sunwin_SWB6109BEV69G_%28iEV10%29_S0S-0224_and_SWB5129BEV77G_%28iEV12T%29_S5A-0069_at_Zhongshan_Rd_%28S-1%29_Xizang_Rd_S.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 52 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/e/ea/Administrative_Division_Shanghai.png/960px-Administrative_Division_Shanghai.png?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 53 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/e/ee/Old_City_of_Shanghai%2C_China_%28December_2015%29_-_13.JPG/250px-Old_City_of_Shanghai%2C_China_%28December_2015%29_-_13.JPG?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 54 | `The Shanghai Grand Theater` | https://upload.wikimedia.org/wikipedia/commons/thumb/f/f0/Shanghaigrandtheatre.jpg/250px-Shanghaigrandtheatre.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 有内容 |
| 55 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/f/f9/Photo_of_St._Ignatius_Cathedral%2C_Shanghai_%E5%9C%A3%E4%BE%9D%E7%BA%B3%E7%88%B5%E4%B8%BB%E6%95%99%E5%BA%A7%E5%A0%82.jpg/250px-Photo_of_St._Ignatius_Cathedral%2C_Shanghai_%E5%9C%A3%E4%BE%9D%E7%BA%B3%E7%88%B5%E4%B8%BB%E6%95%99%E5%BA%A7%E5%A0%82.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 56 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/f/fe/China_blank_province_map.svg/250px-China_blank_province_map.svg.png?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 57 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/f/ff/Shanghai_Government_Building.jpg/250px-Shanghai_Government_Building.jpg?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 为空 |
| 58 | `Official seal of Shanghai` | https://upload.wikimedia.org/wikipedia/en/thumb/f/f1/Shanghai_Municipal_seal.png/120px-Shanghai_Municipal_seal.png?utm_source=en.wikipedia.org&utm_campaign=parser&utm_content=thumbnail | ❌ 否 | alt 有内容 |

### 源 `https://zh.wikipedia.org/wiki/%E5%A4%96%E6%BB%A9`（共 43 张）

| 序号 | 图片 alt 文字 | 图片 URL (img_url) | 是否采纳 | 备注 |
|----:|---------------|---------------------|----------|------|
| 1 | `地图` | https://maps.wikimedia.org/img/osm-intl,13,a,a,270x200.png?lang=zh&domain=zh.wikipedia.org&title=%E5%A4%96%E6%BB%A9&revid=89735334&groups=_3952f07ecdfc6500f25f7a5cec4f919d607fd3d9&parser=legacy | ❌ 否 | alt 有内容 |
| 2 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/0/09/Bank_of_China_Building_The_Bund.JPG/250px-Bank_of_China_Building_The_Bund.JPG | ❌ 否 | alt 为空 |
| 3 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/0/0f/20227-Shanghai%2C_Asia_Building.jpg/250px-20227-Shanghai%2C_Asia_Building.jpg | ❌ 否 | alt 为空 |
| 4 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/1/12/Shanghai_1933.jpg/250px-Shanghai_1933.jpg | ❌ 否 | alt 为空 |
| 5 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/1/1c/Shanghai_Club_Front_View.JPG/250px-Shanghai_Club_Front_View.JPG | ❌ 否 | alt 为空 |
| 6 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/2/20/20112-Shanghai%2C_Broadway_Mansions.jpg/250px-20112-Shanghai%2C_Broadway_Mansions.jpg | ❌ 否 | alt 为空 |
| 7 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/2/22/Shanghai_1928_Bund_Cenotaph.jpeg/250px-Shanghai_1928_Bund_Cenotaph.jpeg | ❌ 否 | alt 为空 |
| 8 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/2/2a/%E5%92%8C%E5%B9%B3%E9%A5%AD%E5%BA%97%E5%8C%97%E6%A5%BC%C2%B7%E5%8D%8E%E6%87%8B%E9%A5%AD%E5%BA%97%C2%B7%E4%B8%8A%E6%B5%B7.jpg/250px-%E5%92%8C%E5%B9%B3%E9%A5%AD%E5%BA%97%E5%8C%97%E6%A5%BC%C2%B7%E5%8D%8E%E6%87%8B%E9%A5%AD%E5%BA%97%C2%B7%E4%B8%8A%E6%B5%B7.jpg | ❌ 否 | alt 为空 |
| 9 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/3/39/The_Bund%2C_1992_%283776324495%29.jpg/250px-The_Bund%2C_1992_%283776324495%29.jpg | ❌ 否 | alt 为空 |
| 10 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/4/40/Shanghaiviewpic6.jpg/250px-Shanghaiviewpic6.jpg | ❌ 否 | alt 为空 |
| 11 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/4/41/The_HSBC_Building_and_the_Customs_House.jpg/250px-The_HSBC_Building_and_the_Customs_House.jpg | ❌ 否 | alt 为空 |
| 12 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/4/45/Bund_at_night_%28with_Bund_Financial_Center%29.jpg/250px-Bund_at_night_%28with_Bund_Financial_Center%29.jpg | ❌ 否 | alt 为空 |
| 13 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/4/46/Yangtze_Building_The_Bund.JPG/250px-Yangtze_Building_The_Bund.JPG | ❌ 否 | alt 为空 |
| 14 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/5/56/2012_Bund_Shanghai.jpg/1920px-2012_Bund_Shanghai.jpg | ❌ 否 | alt 为空 |
| 15 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/6/62/20216-Shanghai%2C_Great_Northern_Telegraph_Building.jpg/250px-20216-Shanghai%2C_Great_Northern_Telegraph_Building.jpg | ❌ 否 | alt 为空 |
| 16 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/6/67/The_Bund%2C_Shanghai%2C_c1890s.jpg/250px-The_Bund%2C_Shanghai%2C_c1890s.jpg | ❌ 否 | alt 为空 |
| 17 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/6/69/The_Bund_No_33.jpg/250px-The_Bund_No_33.jpg | ❌ 否 | alt 为空 |
| 18 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/7/76/Palace_Hotel_The_Bund.JPG/250px-Palace_Hotel_The_Bund.JPG | ❌ 否 | alt 为空 |
| 19 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/8/80/Shanghai_Harbor_1937.jpg/250px-Shanghai_Harbor_1937.jpg | ❌ 否 | alt 为空 |
| 20 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/8/85/China_Bank_of_Communications_Building%2C_Shanghai.JPG/250px-China_Bank_of_Communications_Building%2C_Shanghai.JPG | ❌ 否 | alt 为空 |
| 21 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/8/8c/Chartered_Bank_Building_The_Bund.JPG/250px-Chartered_Bank_Building_The_Bund.JPG | ❌ 否 | alt 为空 |
| 22 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/9/96/Glen_Line_Building_The_Bund.JPG/250px-Glen_Line_Building_The_Bund.JPG | ❌ 否 | alt 为空 |
| 23 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/9/9c/HSBC_Building_The_Bund.JPG/250px-HSBC_Building_The_Bund.JPG | ❌ 否 | alt 为空 |
| 24 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/9/9f/The_Bund%2C_Shanghai%2C_2010-12-16_Night_%28Panorama%29.jpg/1920px-The_Bund%2C_Shanghai%2C_2010-12-16_Night_%28Panorama%29.jpg | ❌ 否 | alt 为空 |
| 25 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/a/a1/Jardine_Matheson_Building_The_Bund.JPG/250px-Jardine_Matheson_Building_The_Bund.JPG | ❌ 否 | alt 为空 |
| 26 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/a/a7/The_Nishin_Navigation_Building_The_Bund.JPG/250px-The_Nishin_Navigation_Building_The_Bund.JPG | ❌ 否 | alt 为空 |
| 27 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/a/ad/20210-Shanghai%2C_China_Merchants_Steam_Navigation_Company.jpg/250px-20210-Shanghai%2C_China_Merchants_Steam_Navigation_Company.jpg | ❌ 否 | alt 为空 |
| 28 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/a/af/Waibaidu_Bridge.jpg/250px-Waibaidu_Bridge.jpg | ❌ 否 | alt 为空 |
| 29 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/b/b6/Shanghai_1884.jpg/250px-Shanghai_1884.jpg | ❌ 否 | alt 为空 |
| 30 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/b/ba/%E6%9C%89%E5%88%A9%E5%A4%A7%E6%A5%BC%E4%B8%9C%E5%8C%97%E7%AB%8B%E9%9D%A2.jpg/250px-%E6%9C%89%E5%88%A9%E5%A4%A7%E6%A5%BC%E4%B8%9C%E5%8C%97%E7%AB%8B%E9%9D%A2.jpg | ❌ 否 | alt 为空 |
| 31 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/b/ba/Yokohama_Specie_Bank_Building_Shanghai.JPG/250px-Yokohama_Specie_Bank_Building_Shanghai.JPG | ❌ 否 | alt 为空 |
| 32 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/b/be/The_Bund_Night_View_Old_Pic.jpg/250px-The_Bund_Night_View_Old_Pic.jpg | ❌ 否 | alt 为空 |
| 33 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/c/c0/Former_Bank_de_l%27Indichina%2C_The_Bund%2C_Shanghai..jpg/250px-Former_Bank_de_l%27Indichina%2C_The_Bund%2C_Shanghai..jpg | ❌ 否 | alt 为空 |
| 34 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/c/c1/Customs_House_20202-Shanghai_%2833006549136%29.jpg/250px-Customs_House_20202-Shanghai_%2833006549136%29.jpg | ❌ 否 | alt 为空 |
| 35 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/c/c2/First_Generation_of_HSBC_Building_on_the_Bund.jpg/250px-First_Generation_of_HSBC_Building_on_the_Bund.jpg | ❌ 否 | alt 为空 |
| 36 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/d/d1/China_Merchants_Bank_Building%2C_Shanghai.JPG/250px-China_Merchants_Bank_Building%2C_Shanghai.JPG | ❌ 否 | alt 为空 |
| 37 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/d/d4/Part_of_the_Bund%2C_Shanghai.jpg/250px-Part_of_the_Bund%2C_Shanghai.jpg | ❌ 否 | alt 为空 |
| 38 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/e/e3/Russo-Chinese_Bank_Building%2C_Shanghai.JPG/250px-Russo-Chinese_Bank_Building%2C_Shanghai.JPG | ❌ 否 | alt 为空 |
| 39 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/e/e5/A_picture_from_China_every_day_101.jpg/250px-A_picture_from_China_every_day_101.jpg | ❌ 否 | alt 为空 |
| 40 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/e/ed/The_Residence_of_the_Consul.JPG/250px-The_Residence_of_the_Consul.JPG | ❌ 否 | alt 为空 |
| 41 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/e/ef/North_China_Daily_News_Building_Shanghai.JPG/250px-North_China_Daily_News_Building_Shanghai.JPG | ❌ 否 | alt 为空 |
| 42 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/f/f9/Bank_of_Taiwan_Building_Shanghai.JPG/250px-Bank_of_Taiwan_Building_Shanghai.JPG | ❌ 否 | alt 为空 |
| 43 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/f/fc/Astor_House_Hotel_%26_Resteraunt_Shanghai.jpg/250px-Astor_House_Hotel_%26_Resteraunt_Shanghai.jpg | ❌ 否 | alt 为空 |

### 源 `https://www.yugarden.com.cn/page/articleView/index.html`（共 14 张）

| 序号 | 图片 alt 文字 | 图片 URL (img_url) | 是否采纳 | 备注 |
|----:|---------------|---------------------|----------|------|
| 1 | `` | https://www.yugarden.com.cn/news/Article/0b83a038-8185-48c2-bf1d-b16b65b10c2a.jpg | ❌ 否 | alt 为空 |
| 2 | `` | https://www.yugarden.com.cn/news/Article/2ad588f8-fd01-4d3c-81b8-ddc38ca9cd96.jpg | ❌ 否 | alt 为空 |
| 3 | `` | https://www.yugarden.com.cn/news/Article/589d008c-f1b5-4d9e-8fbf-4c6933881b35.jpg | ❌ 否 | alt 为空 |
| 4 | `` | https://www.yugarden.com.cn/page/articleView/images/arr.png | ❌ 否 | alt 为空 |
| 5 | `` | https://www.yugarden.com.cn/page/articleView/images/msg-title.png | ❌ 否 | alt 为空 |
| 6 | `` | https://www.yugarden.com.cn/page/articleView/images/news-title.png | ❌ 否 | alt 为空 |
| 7 | `` | https://www.yugarden.com.cn/page/articleView/images/recruit.png | ❌ 否 | alt 为空 |
| 8 | `` | https://www.yugarden.com.cn/page/articleView/images/xjp/play.png | ❌ 否 | alt 为空 |
| 9 | `` | https://www.yugarden.com.cn/page/articleView/images/xjp/xjp-href.png | ❌ 否 | alt 为空 |
| 10 | `` | https://www.yugarden.com.cn/page/articleView/images/xjp/xjp-indexbg.png | ❌ 否 | alt 为空 |
| 11 | `` | https://www.yugarden.com.cn/page/articleView/images/xjp/xjp-title.png | ❌ 否 | alt 为空 |

### 源 `https://zh.wikipedia.org/zh-hans/%E8%B1%AB%E5%9C%92%E5%95%86%E5%9F%8E`（共 10 张）

| 序号 | 图片 alt 文字 | 图片 URL (img_url) | 是否采纳 | 备注 |
|----:|---------------|---------------------|----------|------|
| 1 | `悦宾楼一带` | https://upload.wikimedia.org/wikipedia/commons/thumb/1/14/Yuyuang_market01.JPG/120px-Yuyuang_market01.JPG | ❌ 否 | alt 有内容 |
| 2 | `上海老饭店` | https://upload.wikimedia.org/wikipedia/commons/thumb/6/64/Shanghai_-_panoramio_-_HALUK_COMERTEL_%285%29.jpg/120px-Shanghai_-_panoramio_-_HALUK_COMERTEL_%285%29.jpg | ❌ 否 | alt 有内容 |
| 3 | `豫园商城夜景` | https://upload.wikimedia.org/wikipedia/commons/thumb/6/6d/Shanghai_-_panoramio.jpg/120px-Shanghai_-_panoramio.jpg | ❌ 否 | alt 有内容 |
| 4 | `艺术节街头相声` | https://upload.wikimedia.org/wikipedia/commons/thumb/7/7a/Yu_Yuan_Gardens_and_Bazaar_%283020091016%29.jpg/120px-Yu_Yuan_Gardens_and_Bazaar_%283020091016%29.jpg | ❌ 否 | alt 有内容 |
| 5 | `和丰楼` | https://upload.wikimedia.org/wikipedia/commons/thumb/7/7f/Old_City_of_Shanghai%2C_China_%28December_2015%29_-_10.JPG/120px-Old_City_of_Shanghai%2C_China_%28December_2015%29_-_10.JPG | ❌ 否 | alt 有内容 |
| 6 | `中央广场` | https://upload.wikimedia.org/wikipedia/commons/thumb/8/84/Yuyuan_Tourist_Mart1.JPG/120px-Yuyuan_Tourist_Mart1.JPG | ❌ 否 | alt 有内容 |
| 7 | `老庙金店` | https://upload.wikimedia.org/wikipedia/commons/thumb/e/e3/Yuyuan_Market_Shanghai.JPG/120px-Yuyuan_Market_Shanghai.JPG | ❌ 否 | alt 有内容 |
| 8 | `豫园百货` | https://upload.wikimedia.org/wikipedia/commons/thumb/e/e8/Shanghai-yu_yuan_bazaar._-_panoramio.jpg/120px-Shanghai-yu_yuan_bazaar._-_panoramio.jpg | ❌ 否 | alt 有内容 |
| 9 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/f/ff/Yuyuan_Tourist_Mart_outside.JPG/250px-Yuyuan_Tourist_Mart_outside.JPG | ❌ 否 | alt 为空 |
| 10 | `` | https://upload.wikimedia.org/wikipedia/zh/0/07/Yuyuan.gif | ❌ 否 | alt 为空 |

### 源 `https://en.wikipedia.org/wiki/Oriental_Pearl_Tower`（共 8 张）

| 序号 | 图片 alt 文字 | 图片 URL (img_url) | 是否采纳 | 备注 |
|----:|---------------|---------------------|----------|------|
| 1 | `Map` | https://maps.wikimedia.org/img/osm-intl,13,a,a,250x200.png?lang=en&domain=en.wikipedia.org&title=Oriental_Pearl_Tower&revid=1357282250&groups=_485525318c2e5291001d74ee4567b5fd57ac1bda&parser=parsoid | ❌ 否 | alt 有内容 |
| 2 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/4/4d/Pudong%2C_August_2012_04.JPG/250px-Pudong%2C_August_2012_04.JPG | ❌ 否 | alt 为空 |
| 3 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/6/6d/Oriental_Pearl_Tower_20251126.jpg/330px-Oriental_Pearl_Tower_20251126.jpg | ❌ 否 | alt 为空 |
| 4 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/6/6e/Tallest_towers_in_the_world.svg/langor-250px-Tallest_towers_in_the_world.svg.png | ❌ 否 | alt 为空 |
| 5 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/7/71/Huangpu_River%2COriental_Pearl_Tower%2CThe_Bund%2CShanghai%2CChina.jpg/250px-Huangpu_River%2COriental_Pearl_Tower%2CThe_Bund%2CShanghai%2CChina.jpg | ❌ 否 | alt 为空 |
| 6 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/7/7c/Shanghaiatnightpic1.jpg/120px-Shanghaiatnightpic1.jpg | ❌ 否 | alt 为空 |
| 7 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/8/8e/Oriental_Pearl_Tower_Observation_Deck.jpg/120px-Oriental_Pearl_Tower_Observation_Deck.jpg | ❌ 否 | alt 为空 |
| 8 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/a/af/Russia_stamp_1998_%E2%84%96_471.jpg/250px-Russia_stamp_1998_%E2%84%96_471.jpg | ❌ 否 | alt 为空 |

### 源 `https://www.thepaper.cn/newsDetail_forward_24998814`（共 5 张）

| 序号 | 图片 alt 文字 | 图片 URL (img_url) | 是否采纳 | 备注 |
|----:|---------------|---------------------|----------|------|
| 1 | `` | https://www.thepaper.cn/_next/static/media/label_sm_90030.2e849b63.png | ❌ 否 | alt 为空 |
| 2 | `` | https://www.thepaper.cn/_next/static/media/pp_report.644295c3.png | ❌ 否 | alt 为空 |
| 3 | `` | https://www.thepaper.cn/_next/static/media/scalecode.ed629179.png | ❌ 否 | alt 为空 |
| 4 | `` | https://www.thepaper.cn/_next/static/media/wechat.ebe50fdd.png | ❌ 否 | alt 为空 |
| 5 | `` | https://www.thepaper.cn/_next/static/media/wuzhangai.a66118af.png | ❌ 否 | alt 为空 |

### 源 `https://en.wikipedia.org/wiki/List_of_tourist_attractions_in_Shanghai`（共 2 张）

| 序号 | 图片 alt 文字 | 图片 URL (img_url) | 是否采纳 | 备注 |
|----:|---------------|---------------------|----------|------|
| 1 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/3/36/Shanghai_at_night.jpg/250px-Shanghai_at_night.jpg | ❌ 否 | alt 为空 |
| 2 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/4/45/The_Bund-%60.JPG/250px-The_Bund-%60.JPG | ❌ 否 | alt 为空 |

### 源 `https://en.wikipedia.org/wiki/Tianzifang`（共 2 张）

| 序号 | 图片 alt 文字 | 图片 URL (img_url) | 是否采纳 | 备注 |
|----:|---------------|---------------------|----------|------|
| 1 | `Map` | https://maps.wikimedia.org/img/osm-intl,14,31.210228,121.46413,220x200.png?lang=en&domain=en.wikipedia.org&title=Tianzifang&revid=1348412348&groups=_72288d9c3d03aed3166150119e2175d79c9efee6&parser=parsoid | ❌ 否 | alt 有内容 |
| 2 | `` | https://upload.wikimedia.org/wikipedia/commons/thumb/9/90/Shanghai_Tianzifang_%E4%B8%8A%E6%B5%B7%E7%94%B0%E5%AD%90%E5%9D%8A_-_panoramio.jpg/250px-Shanghai_Tianzifang_%E4%B8%8A%E6%B5%B7%E7%94%B0%E5%AD%90%E5%9D%8A_-_panoramio.jpg | ❌ 否 | alt 为空 |

## 三、阶段事件汇总（来自 IMG-TRACE）

| 事件 | 数值 |
|------|------|
| `FETCHED_IMG` 事件总数 | 238 |
| 唯一源 URL | 9 |
| 唯一 img_url | 235 |
| `LANGGRAPH_FILLED_IMG` | 0 |
| `DEFERRED_FETCHED_IMG` | 0 |
| `DEFERRED_FILL cited=59 already_html=1 to_fetch=0` | 1 |
| `CITATION_INDEX nums=59 sections=121 html_covered=4` | 1 |
| `CITATION_MATCH num=3 imgs=8 kept=0` | 2 次（均落选） |
| `ELIGIBLE_BANK total=0` | 1 |
| `INSERT placements=0` | 1（隐含，未见显式事件，因 ELIGIBLE=0） |
| `PERSIST chosen=0` | 1（隐含） |
| `END status=empty` | 1 |

## 四、本次 run 与上次 (326cee3c…) 对比

| 维度 | 上次（Aug 2-3） | 本次（Aug 4） |
|---|---|---|
| markdown_len | 357,731 | 32,576 |
| 引用编号数 (nums) | 152 | 59 |
| 章节数 (sections) | 187 | 121 |
| html_covered | 20 | 4 |
| 解析图片 (FETCHED) | — | 238 |
| langgraph fill 总数 | 1,855 / 2,560 | 0 (deferred to_fetch=0) |
| ELIGIBLE_BANK | 1 | 0 |
| INSERT placements | 1 | 0 |
| END status | ok | **empty** |

> 关键差异：本 run 由于 `to_fetch=0`（langgraph 没产生需要补图的源），整条图片增强链路只是扫了一遍 9 个源、
> 把可解析的 238 张图都记了 `FETCHED_IMG`（为五键 schema 提供 alt/url 上下文），但 `CITATION_MATCH` 只对
> `num=3` 触发了 2 次且 8 张候选图全部因 `low_similarity=1` 落选，最终 0 张图进入 ELIGIBLE_BANK 与 PERSIST，
> 所以**本次 Markdown 未采纳任何插图**。