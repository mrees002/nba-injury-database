"""
NBA Injury Data Processing Pipeline
====================================

This script processes raw NBA injury data from two sources:
1. IL Transactions (official injured list placements)
2. Missed Games Due to Injury (game-by-game reports)

It combines, deduplicates, classifies, and outputs a clean injury dataset.

USAGE:
    python process_injuries_pipeline.py [il_file] [missed_games_file] [output_file]

EXAMPLE:
    python process_injuries_pipeline.py \
        NBA_IL_combined.csv \
        NBA_Missed_Games_combined.csv \
        nba_injuries_merged.csv
"""

import pandas as pd
import sys
from datetime import datetime


# ============================================================================
# CONFIGURATION
# ============================================================================

# Time windows for deduplication by injury severity (in days)
TIME_WINDOWS = {
    'severe_tears': 365,      # ACL, Achilles, PCL tears
    'surgery': 180,            # Surgery (scheduled after diagnosis)
    'achilles_injury': 90,     # Chronic Achilles issues
    'standard': 30             # Sprains, strains, soreness, etc.
}

# Deduplication scoring (when injury appears in both IL and Missed Games)
DEDUP_SCORES = {
    'il_dataset': 1000,        # IL is more authoritative
    'placed_on_il': 500,       # Official IL placement more reliable
    'notes_length': 'variable' # Longer descriptions preferred
}

# Body parts tracked (32 total)
BODY_PARTS = {
    'knee', 'ankle', 'foot', 'hand', 'wrist', 'shoulder', 'elbow', 'hip',
    'back', 'hamstring', 'quadriceps', 'quad', 'groin', 'thumb', 'finger',
    'toe', 'neck', 'head', 'shin', 'thigh', 'abductor', 'plantaris',
    'leg', 'arm', 'chest', 'rib', 'abdomen', 'abdominal', 'eye',
    'nose', 'mouth', 'jaw', 'illness', 'achilles'
}

# Injury types tracked (45 total)
INJURY_TYPES = {
    'ACL tear', 'MCL tear', 'PCL tear', 'ACL injury', 'MCL injury', 'PCL injury',
    'tear', 'strain', 'sprain', 'fracture', 'bruise', 'contusion', 'surgery',
    'soreness', 'stiffness', 'tightness', 'inflammation', 'spasm',
    'plantar fasciitis', 'hyperextension', 'turf toe', 'stress reaction',
    'subluxation', 'impingement', 'bursitis', 'infection', 'tendinopathy',
    'synovitis', 'blood clot', 'corneal abrasion', 'shin splints',
    'nerve issue', 'disc injury', 'loose bodies', 'scar tissue', 'cyst',
    'concussion', 'hernia', 'retinal injury', 'dislocation', 'swelling',
    'laceration', 'achilles injury', 'injury', 'illness', 'non-injury',
    'rehab', 'rest', 'recovery'
}


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def is_recovery_note(notes_text):
    """Check if notes indicate recovery/surgery followup rather than new injury"""
    if pd.isna(notes_text):
        return False
    
    notes_lower = str(notes_text).lower()
    recovery_patterns = [
        'recovering from surgery',
        'placed on il recovering from',
    ]
    return any(pattern in notes_lower for pattern in recovery_patterns)


def extract_injury_info(notes_text):
    """
    Parse free-text notes and extract:
    - body_part: Which part of body is injured
    - injury_type: What type of injury
    
    Uses hierarchical keyword matching.
    """
    if pd.isna(notes_text):
        return None, None
    
    notes_lower = str(notes_text).lower()
    body_part = None
    injury_type = None
    
    # STEP 1: Identify recovery notes
    if is_recovery_note(notes_text):
        if 'achilles' in notes_lower:
            body_part = 'achilles'
        elif 'acl' in notes_lower or 'knee' in notes_lower:
            body_part = 'knee'
        elif 'calf' in notes_lower:
            body_part = 'calf'
        injury_type = 'recovery'
        return body_part, injury_type
    
    # STEP 2: Check for ACL/MCL/PCL injuries
    if 'acl' in notes_lower or 'anterior cruciate ligament' in notes_lower:
        body_part = 'knee'
        injury_type = 'ACL tear' if any(w in notes_lower for w in ['torn', 'tear', 'ruptured', 'reconstruct']) else 'ACL injury'
    
    elif 'mcl' in notes_lower or 'medial collateral ligament' in notes_lower:
        body_part = 'knee'
        injury_type = 'MCL tear' if any(w in notes_lower for w in ['torn', 'tear', 'ruptured']) else 'MCL injury'
    
    elif 'pcl' in notes_lower or 'posterior cruciate ligament' in notes_lower:
        body_part = 'knee'
        injury_type = 'PCL tear' if any(w in notes_lower for w in ['torn', 'tear', 'ruptured']) else 'PCL injury'
    
    # STEP 3: Check for Achilles injuries
    elif 'achilles' in notes_lower:
        body_part = 'achilles'
        injury_type = 'tear' if any(w in notes_lower for w in ['torn', 'tear', 'ruptured']) else 'achilles injury'
    
    # STEP 4: Check for Calf injuries (special handling to avoid "calf injury" type)
    elif 'calf' in notes_lower:
        body_part = 'calf'
        if 'strain' in notes_lower or 'strained' in notes_lower:
            injury_type = 'strain'
        elif 'tear' in notes_lower or 'torn' in notes_lower:
            injury_type = 'tear'
        elif 'sore' in notes_lower or 'soreness' in notes_lower:
            injury_type = 'soreness'
        elif 'sprain' in notes_lower:
            injury_type = 'sprain'
        else:
            injury_type = 'injury'
    
    # STEP 5: Check for other injuries
    else:
        # Find body part
        body_parts_map = {
            'knee': 'knee', 'ankle': 'ankle', 'foot': 'foot', 'hand': 'hand',
            'wrist': 'wrist', 'shoulder': 'shoulder', 'elbow': 'elbow', 'hip': 'hip',
            'back': 'back', 'hamstring': 'hamstring', 'quadriceps': 'quadriceps',
            'quad': 'quadriceps', 'groin': 'groin', 'thumb': 'thumb', 'finger': 'finger',
            'toe': 'toe', 'neck': 'neck', 'head': 'head', 'shin': 'shin',
            'thigh': 'thigh', 'abductor': 'abductor', 'plantaris': 'plantaris',
            'leg': 'leg', 'arm': 'arm', 'chest': 'chest', 'rib': 'rib',
            'abdomen': 'abdomen', 'abdominal': 'abdomen', 'eye': 'eye',
            'nose': 'nose', 'mouth': 'mouth', 'jaw': 'jaw'
        }
        
        for key, value in body_parts_map.items():
            if key in notes_lower:
                body_part = value
                break
        
        # Find injury type (hierarchical matching)
        if 'plantar fasci' in notes_lower:
            injury_type = 'plantar fasciitis'
        elif 'hyperextend' in notes_lower:
            injury_type = 'hyperextension'
        elif 'turf toe' in notes_lower:
            injury_type = 'turf toe'
        elif 'stress reaction' in notes_lower or 'stress fracture' in notes_lower:
            injury_type = 'stress reaction'
        elif 'sublux' in notes_lower:
            injury_type = 'subluxation'
        elif 'impingement' in notes_lower:
            injury_type = 'impingement'
        elif 'bursitis' in notes_lower:
            injury_type = 'bursitis'
        elif 'infection' in notes_lower or 'infected' in notes_lower:
            injury_type = 'infection'
        elif 'tendinopathy' in notes_lower or 'tendinosis' in notes_lower:
            injury_type = 'tendinopathy'
        elif 'synovitis' in notes_lower:
            injury_type = 'synovitis'
        elif 'blood clot' in notes_lower:
            injury_type = 'blood clot'
        elif 'corneal abrasion' in notes_lower or ('abrasion' in notes_lower and body_part == 'eye'):
            injury_type = 'corneal abrasion'
        elif 'shin split' in notes_lower:
            injury_type = 'shin splints'
        elif 'nerve' in notes_lower or 'pinched' in notes_lower:
            injury_type = 'nerve issue'
        elif 'bulging disc' in notes_lower or ('disc' in notes_lower and body_part == 'back'):
            injury_type = 'disc injury'
        elif 'loose' in notes_lower and ('cartilage' in notes_lower or 'bodies' in notes_lower):
            injury_type = 'loose bodies'
        elif 'scar tissue' in notes_lower:
            injury_type = 'scar tissue'
        elif 'cyst' in notes_lower:
            injury_type = 'cyst'
        elif 'concussion' in notes_lower:
            injury_type = 'concussion'
        elif 'hernia' in notes_lower:
            injury_type = 'hernia'
        elif 'retina' in notes_lower:
            injury_type = 'retinal injury'
        elif 'dislocate' in notes_lower or 'separated' in notes_lower:
            injury_type = 'dislocation'
        elif 'contusion' in notes_lower or 'pointer' in notes_lower:
            injury_type = 'contusion'
        elif 'swelling' in notes_lower or 'swollen' in notes_lower:
            injury_type = 'swelling'
        elif 'laceration' in notes_lower or 'lacerated' in notes_lower:
            injury_type = 'laceration'
        # Generic injury types
        elif any(w in notes_lower for w in ['torn', 'tear', 'ruptured']):
            injury_type = 'tear'
        elif 'strain' in notes_lower or 'strained' in notes_lower:
            injury_type = 'strain'
        elif 'sprain' in notes_lower or 'sprained' in notes_lower:
            injury_type = 'sprain'
        elif 'fracture' in notes_lower or 'broken' in notes_lower:
            injury_type = 'fracture'
        elif 'surgery' in notes_lower:
            injury_type = 'surgery'
        elif 'bruise' in notes_lower or 'bruised' in notes_lower:
            injury_type = 'bruise'
        elif any(w in notes_lower for w in ['illness', 'flu', 'virus', 'sick', 'migraine', 'covid']):
            body_part = 'illness'
            injury_type = 'illness'
        elif 'sore' in notes_lower or 'soreness' in notes_lower:
            injury_type = 'soreness'
        elif 'stiff' in notes_lower or 'stiffness' in notes_lower:
            injury_type = 'stiffness'
        elif any(w in notes_lower for w in ['inflammation', 'tendinitis', 'irritation', 'inflamed']):
            injury_type = 'inflammation'
        elif 'tightness' in notes_lower:
            injury_type = 'tightness'
        elif 'spasm' in notes_lower:
            injury_type = 'spasm'
        elif any(w in notes_lower for w in ['legal', 'fined', 'coach']):
            injury_type = 'non-injury'
        elif 'rehab' in notes_lower:
            injury_type = 'rehab'
        elif 'rest' in notes_lower:
            injury_type = 'rest'
        else:
            injury_type = 'injury'
    
    return body_part, injury_type


def process_dataset(df, source_name):
    """
    Process raw CSV dataset and extract injury information
    
    Args:
        df: DataFrame with Date, Team, Acquired, Relinquished, Notes columns
        source_name: 'NBA_IL' or 'NBA_Missed_Games'
    
    Returns:
        DataFrame with extracted injury data
    """
    injuries = []
    
    for idx, row in df.iterrows():
        # Skip "Acquired" rows (player return from injury)
        if pd.notna(row['Acquired']) and pd.isna(row['Relinquished']):
            continue
        
        # Process "Relinquished" rows (injury occurrences)
        if pd.notna(row['Relinquished']):
            player = str(row['Relinquished']).strip().lstrip('•').strip()
            date = row['Date']
            team = row['Team']
            notes = row['Notes']
            
            body_part, injury_type = extract_injury_info(notes)
            
            injuries.append({
                'date': date,
                'player_name': player,
                'team': team,
                'body_part': body_part,
                'injury_type': injury_type,
                'notes': notes,
                'source': source_name,
                'notes_length': len(str(notes)) if pd.notna(notes) else 0
            })
    
    return pd.DataFrame(injuries)


def get_nba_season(date):
    """
    Convert date to NBA season format
    
    Oct-Dec: Current year season (e.g., Oct 2020 → 2020-21)
    Jan-Sep: Previous year season (e.g., Mar 2021 → 2020-21)
    """
    year = date.year
    month = date.month
    
    if month >= 10:
        return f"{year}-{str(year + 1)[-2:]}"
    else:
        return f"{year - 1}-{str(year)[-2:]}"


# ============================================================================
# MAIN PROCESSING PIPELINE
# ============================================================================

def process_injuries(il_file, missed_games_file, output_file=None):
    """
    Main pipeline: Load, process, deduplicate, and output injury data
    
    Args:
        il_file: Path to IL Transactions CSV
        missed_games_file: Path to Missed Games Due to Injury CSV
        output_file: Path for output CSV (optional)
    
    Returns:
        DataFrame with processed injuries
    """
    
    print("="*80)
    print("NBA INJURY DATA PROCESSING PIPELINE")
    print("="*80)
    print()
    
    # PHASE 1: Load and process both datasets
    print("PHASE 1: Loading and processing datasets...")
    df_il = pd.read_csv(il_file)
    df_missed = pd.read_csv(missed_games_file)
    
    print(f"  IL: {len(df_il):,} records")
    print(f"  Missed Games: {len(df_missed):,} records")
    
    df_il_processed = process_dataset(df_il, 'NBA_IL')
    df_missed_processed = process_dataset(df_missed, 'NBA_Missed_Games')
    
    print(f"  Processed: {len(df_il_processed):,} + {len(df_missed_processed):,} = {len(df_il_processed) + len(df_missed_processed):,}")
    print()
    
    # PHASE 2: Union datasets
    print("PHASE 2: Combining datasets...")
    df_merged = pd.concat([df_il_processed, df_missed_processed], ignore_index=True)
    df_merged['date'] = pd.to_datetime(df_merged['date'])
    
    print(f"  Total records: {len(df_merged):,}")
    print()
    
    # PHASE 3: Exact duplicate removal with scoring
    print("PHASE 3: Removing exact duplicates (IL preferred)...")
    
    def score_record(row):
        """Score records to keep best version"""
        score = 0
        if row['source'] == 'NBA_IL':
            score += 1000
        if pd.notna(row['notes']) and 'placed on il' in str(row['notes']).lower():
            score += 500
        score += row['notes_length']
        return score
    
    df_merged['dedup_score'] = df_merged.apply(score_record, axis=1)
    df_exact_dedup = df_merged.sort_values('dedup_score', ascending=False).drop_duplicates(
        subset=['date', 'player_name', 'team', 'body_part', 'injury_type'],
        keep='first'
    )
    
    print(f"  After dedup: {len(df_exact_dedup):,}")
    print()
    
    # PHASE 4: Remove recovery notes
    print("PHASE 4: Removing recovery notes...")
    df_no_recovery = df_exact_dedup[df_exact_dedup['injury_type'] != 'recovery'].copy()
    
    print(f"  After removal: {len(df_no_recovery):,}")
    print()
    
    # PHASE 5: Time-window deduplication
    print("PHASE 5: Time-window deduplication...")
    
    def deduplicate_by_body_part_and_time(df):
        """Remove related injuries within time windows"""
        keep_indices = []
        
        for player in df['player_name'].unique():
            player_df = df[df['player_name'] == player]
            
            for body_part in player_df['body_part'].unique():
                bp_df = player_df[player_df['body_part'] == body_part]
                bp_df = bp_df.sort_values('date')
                bp_indices = bp_df.index.tolist()
                
                if len(bp_indices) == 0:
                    continue
                
                first_injury_type = bp_df.loc[bp_indices[0], 'injury_type']
                
                # Determine time window
                if first_injury_type in ['ACL tear', 'MCL tear', 'PCL tear', 'tear'] and body_part in ['knee', 'achilles']:
                    base_window = 365
                elif first_injury_type == 'surgery':
                    base_window = 180
                elif first_injury_type in ['achilles injury', 'fracture']:
                    base_window = 90
                else:
                    base_window = 30
                
                keep_indices.append(bp_indices[0])
                last_kept_date = bp_df.loc[bp_indices[0], 'date']
                last_kept_type = first_injury_type
                
                for idx in bp_indices[1:]:
                    current_date = bp_df.loc[idx, 'date']
                    current_type = bp_df.loc[idx, 'injury_type']
                    days_diff = (current_date - last_kept_date).days
                    
                    # Check if related injury
                    is_related = False
                    if (last_kept_type in ['tear', 'ACL tear', 'MCL tear', 'PCL tear'] and 
                        current_type == 'surgery' and days_diff <= 180):
                        is_related = True
                    elif (last_kept_type == 'tear' and body_part == 'achilles' and
                          current_type == 'achilles injury' and days_diff <= 750):
                        is_related = True
                    elif (last_kept_type == 'achilles injury' and current_type == 'achilles injury' and 
                          days_diff <= 180):
                        is_related = True
                    elif (last_kept_type == 'surgery' and current_type == 'surgery' and 
                          days_diff <= 365):
                        is_related = True
                    
                    if is_related:
                        continue
                    elif days_diff > base_window:
                        keep_indices.append(idx)
                        last_kept_date = current_date
                        last_kept_type = current_type
                        if current_type in ['ACL tear', 'MCL tear', 'PCL tear', 'tear'] and body_part in ['knee', 'achilles']:
                            base_window = 365
                        elif current_type == 'surgery':
                            base_window = 180
                        elif current_type in ['achilles injury', 'fracture']:
                            base_window = 90
                        else:
                            base_window = 30
        
        return df.loc[keep_indices].sort_values('date').reset_index(drop=True)
    
    df_dedup = deduplicate_by_body_part_and_time(df_no_recovery)
    
    print(f"  After dedup: {len(df_dedup):,}")
    print()
    
    # PHASE 6: Add season labels
    print("PHASE 6: Adding season labels...")
    df_dedup['season'] = df_dedup['date'].apply(get_nba_season)
    print()
    
    # PHASE 7: Final output
    print("PHASE 7: Preparing output...")
    df_final = df_dedup[['date', 'season', 'player_name', 'team', 'body_part', 'injury_type', 'notes']].copy()
    
    if output_file:
        df_final.to_csv(output_file, index=False)
        print(f"  Saved: {output_file}")
    
    print()
    print("="*80)
    print("SUMMARY")
    print("="*80)
    print(f"Total injuries: {len(df_final):,}")
    print(f"Date range: {df_final['date'].min().date()} to {df_final['date'].max().date()}")
    print(f"Unique players: {df_final['player_name'].nunique():,}")
    print(f"ACL tears: {len(df_final[df_final['injury_type'] == 'ACL tear'])}")
    print(f"Achilles tears: {len(df_final[(df_final['body_part'] == 'achilles') & (df_final['injury_type'] == 'tear')])}")
    print()
    
    return df_final


# ============================================================================
# COMMAND LINE INTERFACE
# ============================================================================

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("USAGE: python process_injuries_pipeline.py [il_file] [missed_games_file] [output_file]")
        print()
        print("EXAMPLE:")
        print("  python process_injuries_pipeline.py \\")
        print("    NBA_IL_combined.csv \\")
        print("    NBA_Missed_Games_combined.csv \\")
        print("    nba_injuries_merged.csv")
        sys.exit(1)
    
    il_file = sys.argv[1]
    missed_games_file = sys.argv[2]
    output_file = sys.argv[3] if len(sys.argv) > 3 else None
    
    try:
        df_result = process_injuries(il_file, missed_games_file, output_file)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)
