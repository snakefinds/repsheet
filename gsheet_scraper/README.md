# Google Sheet Scraper (Standalone Python CLI)

Scrapes **any Google Sheets spreadsheet**:

- **Public sheets**: no authentication needed
- **Private sheets**: uses a **Google Service Account** JSON key

Outputs JSON items with these keys (matching your site data shape):

- `title`
- `price`
- `img`
- `kakobuy`
- `category`

(`batch` and `picksly` are intentionally not produced.)

## Install

From repo root:

```bash
python -m pip install -r gsheet_scraper/requirements.txt
```

## Usage (public sheet)

```bash
python -m gsheet_scraper "https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit#gid=0" --out data_from_sheet.json
```

Important: Google’s CSV export often **does not include hyperlink targets** (you’ll see `LINK` instead of the real URL).
If your sheet uses clickable `LINK` cells, use **API key mode** to preserve the actual URLs:

```bash
python -m gsheet_scraper "SPREADSHEET_ID" --auth api_key --api-key "YOUR_GOOGLE_API_KEY" --out data.json
```

By default, the scraper will:

- follow `ikako.vip` redirects
- if the final link is `kakobuy.com`, force `affcode=7hjf5`

Disable affiliate processing:

```bash
python -m gsheet_scraper "SPREADSHEET_ID" --no-affcode
```

If you need a specific tab:

```bash
python -m gsheet_scraper "SPREADSHEET_ID" --sheet-name "Sheet1"
```

## Usage (private sheet via service account)

### 1) Create a Service Account + key

In Google Cloud Console:

- Create a project (or reuse one)
- Enable **Google Sheets API**
- Create a **Service Account**
- Create a **JSON key** for that service account and download it (example: `service-account.json`)

### 2) Share the sheet with the service account

Open the sheet in Google Sheets and share it with the service account’s email
(looks like `something@your-project.iam.gserviceaccount.com`) as **Viewer**.

### 3) Run the scraper

```bash
python -m gsheet_scraper "https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit" ^
  --auth service_account ^
  --credentials "C:\path\to\service-account.json" ^
  --out data_from_private_sheet.json
```

## Usage (private sheet you can view: OAuth)

If you have **view-only access on your Google account** (but you can’t share the sheet with a service account), use OAuth:

1) In Google Cloud Console, create an **OAuth Client ID** of type **Desktop app**
2) Download the client secret JSON (example: `oauth-client.json`)
3) Run:

```bash
python -m gsheet_scraper "SPREADSHEET_ID" ^
  --auth oauth ^
  --oauth-client-secret "C:\path\to\oauth-client.json" ^
  --out data_from_private_sheet.json
```

The first run opens a browser login and saves a token to `gsheet_scraper/token.json` for future runs.

## Column headers expected

The scraper reads the first row as headers and tries to map these fields:

- `title`: `title`, `name`, `product`, `item`
- `price`: `price`, `cost`, `usd`
- `img`: `img`, `image`, `photo`, `pic`
- `kakobuy`: `kakobuy`, `ikako`, `link`, `url`
- `category`: `category`, `type`, `group`

Extra columns are ignored.
