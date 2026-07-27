# DOI 文献批量下载器

一个面向 Windows 的随用型图形工具。导入包含“文献编号、作者、年份、期刊和 DOI”的 TXT 文档后，程序会自动按编号块识别 DOI，查找公开可访问或开放获取的 PDF，并生成带颜色标记的下载日志。

> 本项目只访问公开可用或开放获取的全文来源，不绕过登录、付费墙、验证码或其他访问控制。

## 已实现功能

- 识别截图所示的多行参考文献格式，每条文献以 `1.`、`1、`、`1)` 或 `[1]` 开头。
- DOI 可以写成 `https://doi.org/10...`、`doi:10...` 或直接写 `10...`。
- 同一文档中的重复 DOI 自动去重。
- 没有编号时，按 DOI 出现顺序自动编号。
- PDF 文件名固定为：`编号+DOI.pdf`。
  - 例如 DOI `10.1016/j.foreco.2021.119318` 保存为：
    `15+10.1016_j.foreco.2021.119318.pdf`
  - Windows 文件名不能包含 `/`，所以 DOI 中的 `/` 会自动替换为 `_`。
- 按 Unpaywall、OpenAlex、Crossref 和 DOI 公开落地页依次查找 PDF。
- 支持 1–8 个并发下载任务。
- 已存在且大小正常的 PDF 自动跳过。
- 输出 `下载日志.xlsx` 和 `下载日志.csv`。
- Excel 日志中：
  - 下载成功：绿色；
  - 下载失败或未识别 DOI：黄色；
  - 已取消：浅橙色。
- GitHub Actions 自动构建无需安装 Python 的 Windows EXE。

## TXT 格式示例

```text
1. Author, A. A. (2021). Article title. Journal Name, 10(2), 1–10.
https://doi.org/10.1000/example.001

2. Author, B. B. (2022). Another title. Journal Name, 11(3), 20–30.
doi: 10.1000/example.002
```

条目可以跨多行。程序先识别编号，再在该编号与下一个编号之间寻找 DOI，因此不要求作者、年份、期刊字段固定排列。

## 直接下载 EXE

1. 打开仓库的 **Actions** 页面。
2. 进入最新一次 **Build Windows EXE**。
3. 在页面底部下载 `DOI文献批量下载器-Windows` 构建产物。
4. 解压后双击 `DOI文献批量下载器.exe`。

发布 GitHub Release 后，工作流也会把 EXE 自动附加到 Release。

## 使用方法

1. 点击“选择文件”，选择 TXT 文档。
2. 选择下载目录。
3. 填写真实联系邮箱。该邮箱只作为 Unpaywall API 的合规查询参数。
4. 点击“开始识别并下载”。
5. 下载结束后，在下载目录查看 PDF 和 `下载日志.xlsx`。

## 本地运行

需要 Python 3.11 或更高版本：

```bash
python -m pip install -r requirements.txt
python app.py
```

## 本地构建 EXE

双击 `build_exe.bat`，构建结果位于：

```text
dist/DOI文献批量下载器.exe
```

## 下载失败的常见原因

- 文献不是开放获取文献；
- 开放版本尚未被 Unpaywall 或 OpenAlex 收录；
- 出版社链接需要登录、机构订阅或人工验证；
- 服务器临时拒绝访问或网络超时；
- DOI 在原 TXT 中不完整。

失败记录会在 Excel 日志中整行标黄，便于后续人工核查。
