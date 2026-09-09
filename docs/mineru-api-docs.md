# MinerU 文档解析接口文档（整理版）

> 来源：<https://mineru.net/apiManage/docs>
> 整理时间：2026-09-03
> 说明：正文为官方文档内容的结构化重排（接口、参数、示例、错误码均保留原文），末尾「整理备注」为非官方原文的补充说明。

MinerU 提供两套文档解析 API：

- **精准解析 API（v4）**：需 Token，支持单文件/批量、表格/公式/多格式输出
- **Agent 轻量解析 API（v1）**：免登录，IP 限频防滥用，专为 AI Agent 工作流设计

---

## 一、模式对比

| 对比维度 | 🎯 精准解析 API | ⚡ Agent 轻量解析 API |
|---|---|---|
| 是否需要 Token | ✅ 需要 | ❌ 无需（IP 限频） |
| 接口地址 | `/api/v4/extract/task` 或 `/api/v4/file-urls/batch` | `/api/v1/agent/parse/url` 或 `/api/v1/agent/parse/file` |
| 模型版本 | `pipeline`（默认）/ `vlm`（推荐）/ `MinerU-HTML` | 固定 pipeline 轻量模型 |
| 文件大小限制 | ≤ 200MB | ≤ 10MB |
| 页数限制 | ≤ 200 页 | ≤ 20 页 |
| 批量支持 | ✅ 支持（≤ 200 个） | ❌ 单文件 |
| 输出格式 | Zip 包（Markdown、JSON，可导出 docx/html/latex） | 仅 Markdown（CDN 链接） |
| 调用方式 | 异步（提交 → 轮询） | 异步（提交 → 轮询） |

---

## 二、🎯 精准解析 API（v4）

> 需填写 Token（API 管理页面自行创建），支持 pipeline / vlm / MinerU-HTML 三种模型，单文件与批量均支持。

### 2.1 概述

适用于需要高精度、深层次结构化提取的复杂文档：智能识别复杂版式与多模态内容（表格、数学公式、图表、图片、多栏布局）。

**核心特性**：极致精度 / 深度结构化 / 多模态支持 / 复杂版式适应（扫描件、排版混乱、水印干扰）。

**文件限制**

| 限制项 | 限制值 |
|---|---|
| 文件大小上限 | 200 MB |
| 文件页数上限 | 200 页 |
| 支持文件类型 | PDF、图片（png/jpg/jpeg/jp2/webp/gif/bmp）、Doc、Docx、Ppt、PPTx、Xls、Xlsx |

### 2.2 单个文件解析

#### (1) 创建解析任务 `POST /api/v4/extract/task`

注意：

- 单个文件 ≤ 200MB，页数 ≤ 200 页
- 每个账号每天享有 1000 页最高优先级解析额度，超出部分优先级降低
- 因网络限制，github、aws 等国外 URL 会请求超时
- 该接口不支持文件直接上传
- Header 需含 `Authorization: Bearer <Token>`

**Python 示例（pdf/doc/ppt/excel/图片）**

```python
import requests

token = "API管理页面自定创建的token"
url = "https://mineru.net/api/v4/extract/task"
header = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {token}"
}
data = {
    "url": "https://cdn-mineru.openxlab.org.cn/demo/example.pdf",
    "model_version": "vlm"
}

res = requests.post(url, headers=header, json=data)
print(res.status_code)
print(res.json())
print(res.json()["data"])
```

**Python 示例（html 文件）**：同上，仅 `data` 改为 `{"url": "https://****", "model_version": "MinerU-HTML"}`。

**CURL 示例**

```sh
curl --location --request POST 'https://mineru.net/api/v4/extract/task' \
--header 'Authorization: Bearer ***' \
--header 'Content-Type: application/json' \
--header 'Accept: */*' \
--data-raw '{
    "url": "https://cdn-mineru.openxlab.org.cn/demo/example.pdf",
    "model_version": "vlm"
}'
```

**请求体参数**

| 参数 | 类型 | 是否必选 | 示例 | 描述 |
|---|---|---|---|---|
| url | string | 是 | <https://cdn-mineru.openxlab.org.cn/demo/example.pdf> | 文件 URL，支持 .pdf/.doc/.docx/.ppt/.pptx/.xls/.xlsx/图片(png/jpg/jpeg/jp2/webp/gif/bmp)/.html |
| is_ocr | bool | 否 | false | 是否启动 OCR，默认 false，仅对 pipeline、vlm 有效 |
| enable_formula | bool | 否 | true | 是否开启公式识别，默认 true，仅对 pipeline、vlm 有效；对 vlm 模型只影响行内公式 |
| enable_table | bool | 否 | true | 是否开启表格识别，默认 true，仅对 pipeline、vlm 有效 |
| language | string | 否 | ch | 文档语言，默认 `ch`，仅对 pipeline、vlm 有效 |
| data_id | string | 否 | abc | 业务数据 ID（字母/数字/_/-/.，≤128 字符），用于唯一标识 |
| callback | string | 否 | <http://127.0.0.1/callback> | 结果回调 URL（HTTP/HTTPS）；为空则必须轮询 |
| seed | string | 否 | abc | 回调签名随机串（英文/数字/_，≤64 字符）；使用 callback 时必填 |
| extra_formats | [string] | 否 | ["docx","html"] | 额外导出格式，仅支持 docx/html/latex；对 html 源文件无效 |
| page_ranges | string | 否 | 1-200 | 页码范围，逗号分隔：`"2,4-6"`=2,4,5,6；`"2--2"`=第2页到倒数第2页 |
| model_version | string | 否 | vlm | pipeline / vlm / MinerU-HTML，默认 pipeline；html 文件必须指定 MinerU-HTML |
| no_cache | bool | 否 | false | 是否绕过 URL 缓存，默认 false |
| cache_tolerance | int | 否 | 900 | 缓存容忍时间（秒），默认 900；no_cache=false 时有效 |

**回调机制说明**

- `checksum`：由 `用户 uid + seed + content` 拼接后 SHA256；UID 可在个人中心查询，用于防篡改校验
- `content`：JSON 字符串，对应任务查询结果的 `data` 部分
- 回调返回 HTTP 200 视为接收成功，其他状态码视为失败；失败最多重推 5 次

**响应参数**

| 参数 | 类型 | 示例 | 说明 |
|---|---|---|---|
| code | int | 0 | 状态码，成功：0 |
| msg | string | ok | 处理信息，成功："ok" |
| trace_id | string | c876cd60b202f2396de1f9e39a1b0172 | 请求 ID |
| data.task_id | string | a90e6ab6-44f3-4554-b459-b62fe4c6b436 | 提取任务 id |

```json
{
  "code": 0,
  "data": { "task_id": "a90e6ab6-44f3-4554-b4***" },
  "msg": "ok",
  "trace_id": "c876cd60b202f2396de1f9e39a1b0172"
}
```

#### (2) 获取任务结果 `GET /api/v4/extract/task/{task_id}`

```python
import requests

token = "API管理页面自定创建的token"
task_id = "上一步创建任务返回的 task_id"
url = f"https://mineru.net/api/v4/extract/task/{task_id}"
header = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}

res = requests.get(url, headers=header)
print(res.status_code); print(res.json()); print(res.json()["data"])
```

```sh
curl --location --request GET 'https://mineru.net/api/v4/extract/task/{task_id}' \
--header 'Authorization: Bearer *****' --header 'Accept: */*'
```

**响应参数**

| 参数 | 说明 |
|---|---|
| data.task_id | 任务 ID |
| data.data_id | 请求时传入的 data_id（原样返回） |
| data.state | done（完成）/ pending（排队）/ running（解析中）/ failed（失败）/ converting（格式转换中） |
| data.full_zip_url | 结果压缩包；非 html：layout.json=中间结果(middle.json)、**_model.json=模型推理结果(model.json)、**_content_list.json=内容列表(content_list.json)、full.md=Markdown；html：full.md + main.html |
| data.err_msg | 失败原因（state=failed 时有效） |
| data.extract_progress | extracted_pages / start_time / total_pages（state=running 时有效） |

```json
{
  "code": 0,
  "data": {
    "task_id": "47726b6e-46ca-4bb9-******",
    "state": "done",
    "full_zip_url": "https://cdn-mineru.openxlab.org.cn/pdf/018e53ad-d4f1-475d-b380-36bf24db9914.zip",
    "err_msg": ""
  },
  "msg": "ok",
  "trace_id": "c876cd60b202f2396de1f9e39a1b0172"
}
```

### 2.3 批量文件解析

#### (1) 本地文件批量上传 `POST /api/v4/file-urls/batch`

注意：

- 上传链接有效期 24 小时
- 上传时无须设置 Content-Type 请求头
- 上传完成后**无须**再调提交接口，系统自动扫描并提交
- 单次申请链接 ≤ 50 个
- Header 需 `Authorization: Bearer <Token>`

```python
import requests

token = "API管理页面自定创建的token"
url = "https://mineru.net/api/v4/file-urls/batch"
header = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
data = {
    "files": [{"name": "demo.pdf", "data_id": "abcd"}],
    "model_version": "vlm"
}
file_path = ["demo.pdf"]
try:
    response = requests.post(url, headers=header, json=data)
    if response.status_code == 200:
        result = response.json()
        if result["code"] == 0:
            batch_id = result["data"]["batch_id"]
            urls = result["data"]["file_urls"]
            for i in range(0, len(urls)):
                with open(file_path[i], 'rb') as f:
                    res_upload = requests.put(urls[i], data=f)
                    print(f"{urls[i]} upload {res_upload.status_code}")
        else:
            print('apply upload url failed,reason:{}'.format(result["msg"]))
except Exception as err:
    print(err)
```

**请求体参数**（顶层：enable_formula / enable_table / language / callback / seed / extra_formats / model_version，含义同单文件）

| 参数 | 类型 | 是否必选 | 描述 |
|---|---|---|---|
| files[].name | string | 是 | 文件名，强烈建议带正确后缀 |
| files[].is_ocr | bool | 否 | 同单文件 |
| files[].data_id | string | 否 | 同单文件 |
| files[].page_ranges | string | 否 | 同单文件 |

**响应**：`data.batch_id`、`data.file_urls[]`

```json
{
  "code": 0,
  "data": {
    "batch_id": "2bb2f0ec-a336-4a0a-b61a-241afaf9cc87",
    "file_urls": ["https://***"]
  },
  "msg": "ok",
  "trace_id": "c876cd60b202f2396de1f9e39a1b0172"
}
```

#### (2) URL 批量提交 `POST /api/v4/extract/task/batch`

- 单次 ≤ 50 个；单文件 ≤ 200MB / ≤200 页；github、aws 等国外 URL 会超时
- `files[].url` 必填，其余字段（is_ocr/data_id/page_ranges）同单文件；顶层还支持 `no_cache`、`cache_tolerance`

```python
data = {
    "files": [{"url": "https://cdn-mineru.openxlab.org.cn/demo/example.pdf", "data_id": "abcd"}],
    "model_version": "vlm"
}
response = requests.post(url, headers=header, json=data)
batch_id = response.json()["data"]["batch_id"]
```

#### (3) 批量获取任务结果 `GET /api/v4/extract-results/batch/{batch_id}`

**响应**：`data.batch_id` + `data.extract_result[]`，每项含 `file_name`、`state`（done / waiting-file / pending / running / failed / converting）、`err_msg`、`full_zip_url`、`data_id`、`extract_progress`

```json
{
  "code": 0,
  "data": {
    "batch_id": "2bb2f0ec-a336-4a0a-b61a-241afaf9cc87",
    "extract_result": [
      {"file_name": "example.pdf", "state": "done", "err_msg": "",
       "full_zip_url": "https://cdn-mineru.openxlab.org.cn/pdf/018e53ad-....zip"},
      {"file_name": "demo.pdf", "state": "running", "err_msg": "",
       "extract_progress": {"extracted_pages": 1, "total_pages": 2, "start_time": "2025-01-20 11:43:20"}}
    ]
  },
  "msg": "ok",
  "trace_id": "c876cd60b202f2396de1f9e39a1b0172"
}
```

### 2.4 常见错误码（精准解析）

| 错误码 | 说明 | 解决建议 |
|---|---|---|
| A0202 | Token 错误 | 检查 Token 是否正确、是否带 Bearer 前缀，或更换新 Token |
| A0211 | Token 过期 | 更换新 Token |
| -500 | 传参错误 | 确保参数类型及 Content-Type 正确 |
| -10001 | 服务异常 | 稍后再试 |
| -10002 | 请求参数错误 | 检查请求参数格式 |
| -60001 | 生成上传 URL 失败 | 稍后再试 |
| -60002 | 获取匹配的文件格式失败 | 文件名/链接带正确后缀，且为 pdf/doc/docx/ppt/pptx/xls/xlsx/png/jp(e)g |
| -60003 | 文件读取失败 | 检查文件是否损坏并重新上传 |
| -60004 | 空文件 | 上传有效文件 |
| -60005 | 文件大小超出限制 | 最大 200MB |
| -60006 | 文件页数超过限制 | 拆分文件后重试 |
| -60007 | 模型服务暂时不可用 | 稍后重试或联系技术支持 |
| -60008 | 文件读取超时 | 检查 URL 可访问 |
| -60009 | 任务提交队列已满 | 稍后再试 |
| -60010 | 解析失败 | 稍后再试 |
| -60011 | 获取有效文件失败 | 确保文件已上传 |
| -60012 | 找不到任务 | 确保 task_id 有效且未删除 |
| -60013 | 没有权限访问该任务 | 只能访问自己提交的任务 |
| -60014 | 删除运行中的任务 | 运行中的任务暂不支持删除 |
| -60015 | 文件转换失败 | 可手动转为 pdf 再上传 |
| -60016 | 文件转换失败（导出格式） | 尝试其他格式导出或重试 |
| -60017 | 重试次数达到上限 | 等后续模型升级后重试 |
| -60018 | 每日解析任务数量已达上限 | 明日再来 |
| -60019 | html 文件解析额度不足 | 明日再来 |
| -60020 | 文件拆分失败 | 稍后重试 |
| -60021 | 读取文件页数失败 | 稍后重试 |
| -60022 | 网页读取失败 | 可能网络问题或限频，稍后重试 |

---

## 三、⚡ Agent 轻量解析 API（v1）

> 免登录、无需 Token，IP 限频防滥用。专为 OpenClaw 等 AI Agent 场景设计，仅输出 Markdown。

### 3.1 概述

- **无需登录**：IP 限频防滥用，无需 Token
- **轻量快速**：PDF/图片用 pipeline 轻量模型（禁用表格/公式识别，追求最快速度）；Word、PPT 用 Office 原生 API 解析
- **统一输出**：仅 Markdown，返回 CDN 链接
- **双模式提交**：URL 解析与文件上传为独立接口，文件上传采用签名上传模式

**文件限制**：≤ 10MB、≤ 20 页；支持 PDF、图片（png/jpg/jpeg/jp2/webp/gif/bmp）、Docx、PPTx、Xlsx

**IP 限频**：每 IP 每分钟提交请求数有限制，超限返回 HTTP 429

### 3.2 URL 解析 `POST https://mineru.net/api/v1/agent/parse/url`

**请求体（JSON）**

| 参数 | 类型 | 是否必选 | 说明 |
|---|---|---|---|
| url | string | 必填 | 远程文件 URL，支持 PDF、图片、Doc/Docx、PPT/PPTx、Xlsx；不支持 HTML |
| file_name | string | 可选 | 文件名（含扩展名），用于判断类型；不提供则从 URL 解析 |
| language | string | 可选 | 默认 `ch`，仅对 PDF 生效 |
| enable_table | bool | 可选 | 默认 `true`，仅对 PDF 生效 |
| is_ocr | bool | 可选 | 默认 `false`，仅对 PDF 生效 |
| enable_formula | bool | 可选 | 默认 `true`，仅对 PDF 生效 |
| page_range | string | 可选 | 仅对 PDF 有效，支持 `from-to`（如 `1-10`）或单页（如 `5`），不支持逗号分隔复杂格式 |

注意：无需 Authorization 头；请求体为 JSON（不支持 multipart/form-data）

```python
import requests

url = "https://mineru.net/api/v1/agent/parse/url"
data = {
    "url": "https://cdn-mineru.openxlab.org.cn/demo/example.pdf",
    "language": "ch", "page_range": "1-10",
    "enable_table": True, "is_ocr": False, "enable_formula": True
}
res = requests.post(url, json=data)
print(res.json())
```

**响应**：`code` / `msg` / `trace_id` / `data.task_id`

### 3.3 本地文件上传（签名上传）`POST https://mineru.net/api/v1/agent/parse/file`

流程：① 调接口传 `file_name` 等参数 → 拿 `task_id` + OSS 签名上传地址 `file_url` → ② 客户端 `PUT` 上传到 `file_url` → ③ 后端自动检测并开始解析 → ④ 轮询查询结果

```python
import requests

api_url = "https://mineru.net/api/v1/agent/parse/file"
data = {"file_name": "document.pdf", "language": "ch", "page_range": "1-10",
        "enable_table": True, "is_ocr": False, "enable_formula": True}
res = requests.post(api_url, json=data)
result = res.json()
task_id = result["data"]["task_id"]
file_url = result["data"]["file_url"]

with open("document.pdf", "rb") as f:
    put_res = requests.put(file_url, data=f)
    print(f"文件上传状态: {put_res.status_code}")
```

响应示例：

```json
{
  "code": 0,
  "data": {
    "task_id": "a90e6ab6-44f3-4554-b459-b62fe4c6b43605",
    "file_url": "https://oss-mineru.openxlab.org.cn/agent/a90e6ab6-...pdf?Expires=..."
  },
  "msg": "ok",
  "trace_id": "c876cd60b202f2396de1f9e39a1b0172"
}
```

注意：不支持批量上传，每次请求只能上传一个文件。

### 3.4 查询解析结果 `GET https://mineru.net/api/v1/agent/parse/{task_id}`

**响应字段**

| 字段 | 说明 |
|---|---|
| data.task_id | 任务 ID |
| data.state | waiting-file（等待文件上传，仅上传模式）/ uploading（文件下载中）/ pending（排队）/ running（解析中）/ done（完成）/ failed（失败） |
| data.markdown_url | Markdown 结果 CDN 链接（state=done 时有效） |
| data.err_msg | 错误信息（state=failed 时有效） |
| data.err_code | 错误码（state=failed 时有效） |

完成示例：

```json
{
  "code": 0,
  "data": {
    "task_id": "a90e6ab6-44f3-4554-b459-b62fe4c6b43605",
    "state": "done",
    "markdown_url": "https://cdn-mineru.openxlab.org.cn/pdf/a90e6ab6-.../full.md"
  },
  "msg": "ok",
  "trace_id": "c876cd60b202f2396de1f9e39a1b0172"
}
```

失败示例：`err_code: -30003`，`err_msg: "file page count exceeds lightweight API limit (50 pages), please use the standard API"`

### 3.5 完整示例（轮询封装，官方提供）

```python
def poll_result(task_id, timeout=300, interval=3):
    """轮询查询解析结果。"""
    state_labels = {"uploading": "文件下载中", "pending": "排队中",
                    "running": "解析中", "waiting-file": "等待文件上传"}
    start = time.time()
    while time.time() - start < timeout:
        resp = requests.get(f"{BASE_URL}/parse/{task_id}")
        result = resp.json()
        state = result["data"]["state"]
        elapsed = int(time.time() - start)
        if state == "done":
            md_resp = requests.get(result["data"]["markdown_url"])
            return md_resp.text
        if state == "failed":
            print(f"[{elapsed}s] 解析失败: {result['data'].get('err_msg', '未知错误')}")
            return None
        print(f"[{elapsed}s] {state_labels.get(state, state)}...")
        time.sleep(interval)
    print(f"轮询超时 ({timeout}s)，请稍后手动查询 task_id: {task_id}")
    return None
```

其中 `BASE_URL`：

- URL 模式：`https://mineru.net/api/v1/agent`
- 文件上传模式：同上（`parse/file` → `PUT file_url` → `parse/{task_id}`）

### 3.6 Agent 专属错误码

| 错误码 | 说明 | Agent 应对策略 |
|---|---|---|
| -30001 | 文件大小超出轻量接口限制（10MB） | 使用标准 API 或拆分文件 |
| -30002 | 轻量接口不支持该文件类型 | 上传 PDF/图片/Doc/PPT/Excel |
| -30003 | 文件页数超出轻量接口限制 | 使用标准 API 或指定 page_range |
| -30004 | 请求参数错误 | 检查必填参数是否缺失 |

---

## 四、language 取值参考

默认 `ch`。

### Standalone language packs

| Value | Included languages | 说明 |
|---|---|---|
| `ch` | Chinese, English, Chinese Traditional | 中英文（默认） |
| `ch_server` | Chinese, English, Chinese Traditional, Japanese | 繁体、手写体 |
| `en` | English | 纯英文 |
| `japan` | Chinese, English, Chinese Traditional, Japanese | 日文为主 |
| `korean` | Korean, English | 韩文 |
| `chinese_cht` | Chinese, English, Chinese Traditional, Japanese | 繁体中文为主 |
| `ta` | Tamil, English | 泰米尔文 |
| `te` | Telugu, English | 泰卢固文 |
| `ka` | Kannada | 卡纳达文 |
| `el` | Greek, English | 希腊文 |
| `th` | Thai, English | 泰文 |

### Language family packs

| Value | Script/Family | Included languages |
|---|---|---|
| `latin` | Latin script（拉丁语系） | French, German, Afrikaans, Italian, Spanish, Bosnian, Portuguese, Czech, Welsh, Danish, Estonian, Irish, Croatian, Uzbek, Hungarian, Serbian(Latin), Indonesian, Occitan, Icelandic, Lithuanian, Maori, Malay, Dutch, Norwegian, Polish, Slovak, Slovenian, Albanian, Swedish, Swahili, Tagalog, Turkish, Latin, Azerbaijani, Kurdish, Latvian, Maltese, Pali, Romanian, Vietnamese, Finnish, Basque, Galician, Luxembourgish, Romansh, Catalan, Quechua |
| `arabic` | Arabic script（阿拉伯语系） | Arabic, Persian, Uyghur, Urdu, Pashto, Kurdish, Sindhi, Balochi, English |
| `cyrillic` | Cyrillic script（西里尔语系） | Russian, Belarusian, Ukrainian, Serbian(Cyrillic), Bulgarian, Mongolian, Abkhazian, Adyghe, Kabardian, Avar, Dargin, Ingush, Chechen, Lak, Lezghian, Tabasaran, Kazakh, Kyrgyz, Tajik, Macedonian, Tatar, Chuvash, Bashkir, Malian, Moldovan, Udmurt, Komi, Ossetian, Buryat, Kalmyk, Tuvan, Sakha, Karakalpak, English |
| `east_slavic` | East Slavic（东斯拉夫语系） | Russian, Belarusian, Ukrainian, English |
| `devanagari` | Devanagari script（天城文语系） | Hindi, Marathi, Nepali, Bihari, Maithili, Angika, Bhojpuri, Magahi, Santali, Newari, Konkani, Sanskrit, Haryanvi, English |

---

## 五、整理备注（非官方原文，供本项目取舍参考）

- **选型要点**：精准 API 才支持 `vlm` 模型、批量（≤200）、docx/html/latex 导出与 200MB/200 页；轻量 API 免登录但只有 10MB/20 页、单文件、仅 Markdown（CDN 链接），且表格/公式识别被禁用。
- **接入形态**：都是"提交 → 轮询 task_id"（或配置 `callback` 回调，回调需校验 `checksum = SHA256(uid + seed + content)`，失败最多重推 5 次）。
- **与 insurance-agent 的关系**：本项目三份条款 PDF 经实测为**纯文本层**（无扫描页、无有效表格），此前评估结论是 pdfplumber 直抽即可、无需 MinerU；本页文档留档，供后续若引入扫描件/复杂版式条款时参考。
- **实测补充(D57):full.md 标题被拍平**：尊享 e生2025 解析结果为 1×#(主标题)+ 298×##——MinerU 把顶层 # 预留给文档主标题,其余标题均弟兄化成 ##(官方 issue #3135/#3203:标题层级预测不可靠,demo 多级标题是 LLM 后处理)→ **不可依赖 full.md 的 # 层级**;真层级在 zip 内 *_content_list.json / middle.json(块级 type+bbox,reader 已保留 content_list 但未接。见 D57 / scripts/compare_parsers.py)。
