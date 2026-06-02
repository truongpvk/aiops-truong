#!/usr/bin/env python3
"""
Log Analyzer Script
Analyzes log files to extract templates and detect anomalies
"""

import sys
import argparse
from collections import defaultdict
import pandas as pd
import numpy as np
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig


def build_miner(sim_th=0.3, drain_depth=4):
    """Build and return a TemplateMiner instance"""
    config = TemplateMinerConfig()
    config.drain_sim_th = sim_th
    config.drain_depth = drain_depth
    return TemplateMiner(config=config)


def parse_logs(log_lines, sim_th=0.3, drain_depth=4):
    """
    Parse log lines and extract templates
    Returns: log_parsed, templates dataframe, miner
    """
    miner = build_miner(sim_th, drain_depth)
    
    log_parsed = []
    
    for line in log_lines:
        result = miner.add_log_message(line)
        try:
            timestamp = int(line.split(' ')[1])
        except (IndexError, ValueError):
            timestamp = 0
        
        data = {
            'template_id': result['cluster_id'],
            'template': result['template_mined'],
            'log': line,
            'timestamp': timestamp
        }
        log_parsed.append(data)
    
    # Extract templates
    templates = []
    for cluster in miner.drain.clusters:
        data = {
            'id': cluster.cluster_id,
            'count': cluster.size,
            'template': cluster.get_template()
        }
        templates.append(data)
    
    return log_parsed, templates, miner


def analyze_logs(log_file):
    """Main analysis function"""
    
    # Read log file
    log_lines = []
    with open(log_file, 'r') as f:
        for line in f:
            log_lines.append(line.strip())
    
    total_lines = len(log_lines)
    
    # Parse logs
    log_parsed, templates, miner = parse_logs(log_lines, sim_th=0.3)
    
    # Create dataframe
    df = pd.DataFrame(log_parsed)
    
    # === 1. Total lines and unique templates ===
    num_unique_templates = len(templates)
    
    print("=" * 80)
    print("LOG ANALYSIS RESULTS")
    print("=" * 80)
    print(f"\n1. TỔNG SỐ DÒNG: {total_lines}")
    print(f"   SỐ TEMPLATE UNIQUE: {num_unique_templates}")
    
    # === 2. Top-5 templates ===
    print(f"\n2. TOP-5 TEMPLATE:")
    top_templates = sorted(templates, key=lambda x: x['count'], reverse=True)[:5]
    
    for idx, template in enumerate(top_templates, 1):
        percentage = (template['count'] / total_lines) * 100
        print(f"   {idx}. Template ID {template['id']}: {template['count']} lần ({percentage:.2f}%)")
        print(f"      {template['template'][:100]}")
    
    # === 3. Anomaly detection: Templates with sudden spike ===
    print(f"\n3. TEMPLATE TĂNG ĐỘT BIẾN TRONG 1 GIỜ GẦN NHẤT:")
    
    if len(df) > 0 and 'timestamp' in df.columns:
        # Get max timestamp (most recent hour)
        max_timestamp = df['timestamp'].max()
        
        # Define "recent hour" - logs within 1 hour before max timestamp
        # Assuming timestamp is in seconds, 3600 seconds = 1 hour
        recent_threshold = max_timestamp - 3600
        
        # Split data: before and during recent hour
        df_before = df[df['timestamp'] <= recent_threshold]
        df_recent = df[df['timestamp'] > recent_threshold]
        
        recent_counts = df_recent.groupby('template_id').size()
        before_counts = df_before.groupby('template_id').size()
        
        anomalies = []
        for template_id in recent_counts.index:
            recent_count = recent_counts[template_id]
            before_avg = before_counts.get(template_id, 0)
            
            # Detect spike: recent count > 2x average before
            if before_avg > 0 and recent_count > 2 * before_avg:
                template_obj = next((t for t in templates if t['id'] == template_id), None)
                if template_obj:
                    anomalies.append({
                        'template_id': template_id,
                        'template': template_obj['template'],
                        'recent_count': recent_count,
                        'before_count': before_avg,
                        'increase_ratio': recent_count / before_avg if before_avg > 0 else float('inf')
                    })
        
        if anomalies:
            anomalies_sorted = sorted(anomalies, key=lambda x: x['increase_ratio'], reverse=True)
            for idx, anom in enumerate(anomalies_sorted, 1):
                print(f"   {idx}. Template ID {anom['template_id']}: {anom['recent_count']} lần (trước: {anom['before_count']}, tỷ lệ: {anom['increase_ratio']:.2f}x)")
                print(f"      {anom['template'][:100]}")
        else:
            print("   Không phát hiện template nào tăng đột biến.")
    
    # === 4. New templates (appeared only in recent hour) ===
    print(f"\n4. NEW TEMPLATES (chưa xuất hiện trước giờ gần nhất):")
    
    if len(df) > 0 and 'timestamp' in df.columns:
        max_timestamp = df['timestamp'].max()
        recent_threshold = max_timestamp - 3600
        
        df_before = df[df['timestamp'] <= recent_threshold]
        df_recent = df[df['timestamp'] > recent_threshold]
        
        before_templates = set(df_before['template_id'].unique())
        recent_templates = set(df_recent['template_id'].unique())
        
        new_templates_ids = recent_templates - before_templates
        
        if new_templates_ids:
            for template_id in sorted(new_templates_ids):
                template_obj = next((t for t in templates if t['id'] == template_id), None)
                if template_obj:
                    count = len(df_recent[df_recent['template_id'] == template_id])
                    print(f"   - Template ID {template_id}: xuất hiện {count} lần trong giờ gần nhất")
                    print(f"     {template_obj['template'][:100]}")
        else:
            print("   Không có template mới nào xuất hiện.")
    
    print("\n" + "=" * 80)


def main():
    parser = argparse.ArgumentParser(description='Analyze log files and extract templates')
    parser.add_argument('logfile', nargs='?', default='BGL.log', help='Path to log file (default: BGL.log)')
    
    args = parser.parse_args()
    
    try:
        analyze_logs(args.logfile)
    except FileNotFoundError:
        print(f"Error: Log file '{args.logfile}' not found.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
