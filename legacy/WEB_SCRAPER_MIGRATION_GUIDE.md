# Web Scraper Migration Guide
## Automating NBA Injury Dataset Updates

---

## 📁 UPLOADED FILES REFERENCE

All your current data sources that need to be scraped:

### **Base Datasets** (Original upload)
```
1. NBA_IL.csv
   - Original injured list dataset
   - 27,588 records (Oct 2010 - Feb 5, 2026)
   - Source: Official NBA IL transactions

2. NBA_Missed_Games.csv
   - Original missed games dataset  
   - 15,211 records (Oct 2010 - Feb 5, 2026)
   - Source: Game-by-game injury reports
```

### **Incremental Updates** (All subsequent uploads)
```
3. IL_Updated__02-27-26_-_Sheet1.csv
   - Feb 4-25, 2026 (171 records)

4. Missed_Games_due_to_Injury_Updated__02-27-26_-_Sheet1.csv
   - Feb 4-25, 2026 (154 records)

5. IL_Transactions__02-25-2026__03-19-2026.csv
   - Feb 25 - Mar 18, 2026 (281 records)

6. Missed_Games_Due_to_Injury__02-25-2026__03-19-2026.csv
   - Feb 25 - Mar 18, 2026 (185 records)

7. IL_Transactions__03-20-2026__04-14-2026.csv
   - Mar 20 - Apr 12, 2026 (325 records)

8. Missed_Games_Due_to_Injury__03-20-2026__04-14-2026.csv
   - Mar 20 - Apr 12, 2026 (265 records)
```

**Total across all files: 28,365 IL records + 15,815 Missed Games records**

---

## 🔄 CURRENT PROCESSING SCRIPT

**File:** `process_injuries_pipeline.py`

This is the complete, production-ready processing script that needs to be integrated into your web scraper.

### How It Works (7-Phase Pipeline)

```
INPUT: Two CSV files (IL + Missed Games)
  ↓
PHASE 1: Load & Extract
  - Parse CSV files
  - Extract: date, player, team, body_part, injury_type, notes
  ↓
PHASE 2: Union Datasets
  - Combine IL + Missed Games
  - Result: ~26,000 total records
  ↓
PHASE 3: Exact Duplicate Removal
  - Score duplicate records
  - Keep: IL dataset (+1000 pts), "Placed on IL" text (+500 pts), longer notes (+length)
  - Result: ~13,000 records
  ↓
PHASE 4: Recovery Note Filtering
  - Remove entries like "recovering from surgery"
  - Keep: Original injury records only
  ↓
PHASE 5: Time-Window Deduplication
  - Merge related injuries (tear + surgery, revision surgeries, etc.)
  - Windows: 365 days (severe), 180 days (surgery), 90 days (achilles), 30 days (other)
  ↓
PHASE 6: Season Assignment
  - Add NBA season labels (YYYY-YY format)
  - Oct-Dec = Current year; Jan-Sep = Previous year
  ↓
OUTPUT: Clean CSV with 7 columns
  - date, season, player_name, team, body_part, injury_type, notes
```

### Key Functions in Script

**Main Entry Point:**
```python
def process_injuries(il_file, missed_games_file, output_file=None)
```

**Core Functions:**
```python
extract_injury_info(notes_text)
    → Returns: (body_part, injury_type)
    
process_dataset(df, source_name)
    → Returns: DataFrame with extracted injuries

deduplicate_by_body_part_and_time(df)
    → Applies time windows, returns deduplicated DataFrame

get_nba_season(date)
    → Converts date to NBA season format
```

**Configuration Constants (Customizable):**
```python
TIME_WINDOWS = {
    'severe_tears': 365,      # ACL, Achilles, PCL
    'surgery': 180,           # Scheduled surgeries
    'achilles_injury': 90,    # Chronic Achilles
    'standard': 30            # Sprains, strains, soreness
}

DEDUP_SCORES = {
    'il_dataset': 1000,       # IL more authoritative
    'placed_on_il': 500,      # Official designation
    'notes_length': 'variable' # Longer = better
}

BODY_PARTS = {...}  # 32 body parts tracked
INJURY_TYPES = {...} # 45 injury types tracked
```

### Command-Line Usage

```bash
python process_injuries_pipeline.py \
    NBA_IL_combined.csv \
    NBA_Missed_Games_combined.csv \
    nba_injuries_merged.csv
```

---

## 🌐 WEB SCRAPING ARCHITECTURE

For your automated web scraper, follow this pattern:

### Step 1: Data Acquisition
```
→ Identify data sources (Pro/Con analysis sports sites? Basketball Reference?)
→ Determine scraping method (API? Web scraping? RSS feeds?)
→ Handle rate limiting & legal compliance
→ Store raw data as CSVs (IL + Missed Games format)
```

### Step 2: Data Standardization
```
→ Ensure scraped data matches existing CSV format:
  Columns: Date, Team, Acquired, Relinquished, Notes
→ Append to combined files (don't replace)
→ Maintain datetime consistency
```

### Step 3: Run Processing Pipeline
```
→ Call process_injuries() with combined files
→ Generate updated nba_injuries_merged.csv
→ Run quality checks (duplicates, nulls, classifications)
```

### Step 4: Distribution
```
→ Upload to private app/database
→ Version control (date-stamped backups)
→ Trigger notifications on major injuries
```

---

## 📊 CLASSIFICATION SYSTEM

The script classifies injuries into **45 types** across **32 body parts**:

### Body Parts (32)
```
knee, ankle, foot, hand, wrist, shoulder, elbow, hip,
back, hamstring, quadriceps, groin, thumb, finger, toe,
neck, head, shin, thigh, abductor, plantaris, leg, arm,
chest, rib, abdomen, eye, nose, mouth, jaw, achilles, illness
```

### Injury Types (45)
```
Severe: ACL tear, MCL tear, PCL tear, tear, fracture, surgery, dislocation

Moderate: strain, sprain, bruise, contusion, swelling

Minor: soreness, stiffness, tightness, inflammation, spasm

Specific: plantar fasciitis, hyperextension, turf toe, stress reaction, subluxation,
         impingement, bursitis, infection, tendinopathy, synovitis, blood clot,
         corneal abrasion, shin splints, nerve issue, disc injury, loose bodies,
         scar tissue, cyst, concussion, hernia, retinal injury, laceration,
         achilles injury

Other: illness, non-injury, rehab, rest, recovery, injury
```

---

## ⚙️ LEGAL CONSIDERATIONS FOR PRIVATE APP

### Data Privacy & Copyright
- **Don't scrape game statistics** (copyrighted content)
- **Scrape injury reports only** (factual, not creative content)
- **Consider rate limiting** (don't overload servers)
- **Check Terms of Service** of any site you scrape
- **Make app private** (avoid public redistribution)

### Safe Data Sources
- ✅ NBA's official injury updates
- ✅ Official team health/status reports
- ✅ Sports reference databases (if TOS allows)
- ⚠️ Sports news sites (check TOS for scraping)
- ❌ Licensed/paywalled content

### Recommendations
1. **Don't scrape** - API is better. Check if data source has an API.
2. **Respect robots.txt** - Follow site's scraping guidelines
3. **Cache aggressively** - Don't hit servers constantly
4. **Rate limit** - 1 request per second minimum
5. **User-Agent** - Identify your bot clearly
6. **Monitor for changes** - Site structure changes break scrapers

---

## 🚀 DEPLOYMENT CHECKLIST

For your private app implementation:

### Data Pipeline
- [ ] Identify data source(s)
- [ ] Build scraper/API client
- [ ] Test with sample data
- [ ] Implement error handling
- [ ] Add logging/monitoring
- [ ] Schedule automated runs (daily? weekly?)

### Processing
- [ ] Use `process_injuries_pipeline.py` 
- [ ] Customize TIME_WINDOWS if needed
- [ ] Add quality checks post-processing
- [ ] Version control outputs (date-stamped)

### Storage
- [ ] Database (SQL) or file system (CSVs)?
- [ ] Backup strategy
- [ ] Data retention policy
- [ ] Access controls (private!)

### Deployment
- [ ] Web app frontend (React? Vue?)
- [ ] Authentication/authorization
- [ ] Search/filter interface
- [ ] Export functionality
- [ ] Mobile responsive design

### Monitoring
- [ ] Daily update verification
- [ ] Scraper failure alerts
- [ ] Data quality dashboards
- [ ] Usage analytics

---

## 📝 NOTES FOR WEB SCRAPER DEVELOPMENT

### Why This Pipeline Works
1. **Dual-source resilience** - Two data sources mean less downtime
2. **Intelligent deduplication** - Scores each record, keeps best version
3. **Temporal logic** - Time windows prevent over-merging injuries
4. **Flexible classification** - Hierarchical matching handles variations
5. **Clean output** - Normalized format ready for analysis

### Customization Points
- **TIME_WINDOWS** - Adjust based on injury recovery data
- **DEDUP_SCORES** - Change if your scraper favors different sources
- **Classification rules** - Add/modify keyword patterns
- **Season logic** - Currently hardcoded for Oct-Sep; adjust if needed

### Integration Pattern
```python
# Pseudo-code for your app
while True:
    il_data = scrape_il_data()  # Your scraper
    missed_data = scrape_missed_games()  # Your scraper
    
    append_to_files(il_data, missed_data)  # Add to existing
    
    df_clean = process_injuries(
        'NBA_IL_combined.csv',
        'NBA_Missed_Games_combined.csv',
        'nba_injuries_merged.csv'
    )
    
    update_app_database(df_clean)
    
    sleep(24 hours)  # Daily updates
```

---

## 📞 SUPPORT RESOURCES

**Current Dataset Stats:**
- 13,410 total injuries
- 1,505 unique players
- 16 seasons of data
- 1,181 injuries in current season

**No issues found** - Dataset is production-ready for scraper integration!

---

**Good luck with your private app!** 🚀
