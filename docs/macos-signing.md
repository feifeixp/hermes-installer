# macOS 代码签名 + 公证配置指南

> 目标：让 `Hermes-Installer-macOS.dmg` 被 Apple 签名并公证，用户**双击直接安装**，
> 不再出现「无法打开，因为无法验证开发者」「已损坏」等 Gatekeeper 拦截。

CI（`.github/workflows/build.yml` 的 `macos` job）已经写好签名 + 公证流水线。
它**只在下面 5 个 GitHub Secret 都配置后才生效**；没配时自动回退到未签名构建，
不会让 CI 失败。你只需要拿到凭证、配进 Secrets，下一次 `vX.Y.Z` tag 构建即自动签名公证。

---

## 前置：Apple Developer Program 会员（$99/年）

如果还没有：https://developer.apple.com/programs/ → Enroll（个人或公司）。
审核通过后才能创建下面的 Developer ID 证书。

---

## 需要配置的 5 个 GitHub Secret

仓库 → Settings → Secrets and variables → Actions → New repository secret。

| Secret 名 | 内容 |
|---|---|
| `APPLE_CERT_P12_BASE64` | Developer ID Application 证书（含私钥）的 `.p12`，base64 编码后的文本 |
| `APPLE_CERT_PASSWORD` | 你导出 `.p12` 时设置的密码 |
| `APPLE_KEYCHAIN_PASSWORD` | 任意一串密码（CI 用它建临时钥匙串，自己定即可，如 `hermes-ci-kc-9f3a`） |
| `APPLE_API_KEY_P8_BASE64` | App Store Connect API Key 的 `.p8`，base64 编码后的文本 |
| `APPLE_API_KEY_ID` | 该 API Key 的 Key ID（10 位） |
| `APPLE_API_ISSUER_ID` | App Store Connect 的 Issuer ID（UUID 形式） |

> 注：上表是 6 行，因为 API Key 拆成 3 个值。证书相关 2 个 + keychain 1 个 + API Key 3 个 = 6 个 Secret。

---

## 步骤 1 — 创建 & 导出 Developer ID Application 证书

1. 打开「钥匙串访问」→ 菜单「证书助理」→「从证书颁发机构请求证书」
   - 邮箱填你的 Apple ID；「存储到磁盘」生成一个 `CertificateSigningRequest.certSigningRequest`。
2. https://developer.apple.com/account/resources/certificates/list →「+」
   → 选 **Developer ID Application** → 上传上一步的 CSR → 下载 `developerID_application.cer`。
3. 双击 `.cer` 导入「钥匙串访问」（登录钥匙串）。
4. 在钥匙串里找到这张证书，**展开它**确认下面挂着一把私钥；右键证书 →「导出」
   → 格式选 **个人信息交换 (.p12)** → 设一个密码（= `APPLE_CERT_PASSWORD`）→ 保存为 `cert.p12`。
5. 转成 base64：
   ```bash
   base64 -i cert.p12 | pbcopy   # 已复制到剪贴板，直接粘进 APPLE_CERT_P12_BASE64
   ```

---

## 步骤 2 — 创建 App Store Connect API Key（用于公证）

1. https://appstoreconnect.apple.com/access/integrations/api →「Team Keys」→「+」
   - 名称随意，**Access 选 Developer**（公证够用）。
2. 创建后**只能下载一次** `AuthKey_XXXXXXXXXX.p8`，妥善保存。
3. 记下两个值：
   - **Key ID** = 文件名里的 `XXXXXXXXXX`（= `APPLE_API_KEY_ID`）。
   - **Issuer ID** = 该页面顶部的 Issuer ID（UUID，= `APPLE_API_ISSUER_ID`）。
4. 转 base64：
   ```bash
   base64 -i AuthKey_XXXXXXXXXX.p8 | pbcopy   # 粘进 APPLE_API_KEY_P8_BASE64
   ```

---

## 步骤 3 — 把 6 个值填进 GitHub Secrets

按上表逐个「New repository secret」粘贴。`APPLE_KEYCHAIN_PASSWORD` 自己随便定一串。

---

## 步骤 4 — 触发一次签名构建验证

配置完后，重新打一个 tag（或在 Actions 里对 `Build` workflow 手动 `Run workflow`）：

```bash
git tag -a vX.Y.Z -m "..." <main-sha> && git push origin vX.Y.Z
```

构建日志里 `Detect signing availability` 会显示 *Apple signing secrets present*，
随后 `Codesign .app` / `Sign + notarize + staple .dmg` 步骤执行。完成后下载发布页的
`.dmg`，本地验证：

```bash
spctl -a -t open --context context:primary-signature -vv ~/Downloads/Hermes-Installer-macOS.dmg
# 期望输出：... accepted  source=Notarized Developer ID
```

---

## 故障排查

- **`no 'Developer ID Application' identity found`**：导出的 `.p12` 不含私钥，或导出的是
  「Apple Development」而非「Developer ID Application」证书。重做步骤 1.4，确认证书下挂着私钥。
- **`notarytool` 失败 / Invalid credentials**：检查 Key ID / Issuer ID / `.p8` 是否对应同一把
  Key；API Key 的 Access 至少要 Developer。
- **公证通过但 Gatekeeper 仍拦**：确认 `xcrun stapler staple` 成功（票据钉进 DMG 才能离线验证）。

---

## 临时绕过（在签名生效前，给已下载未签名版的用户）

```bash
xattr -dr com.apple.quarantine ~/Downloads/Hermes-Installer-macOS.dmg
# 挂载、拖进 Applications 后：
xattr -dr com.apple.quarantine "/Applications/Hermes Installer.app"
```
