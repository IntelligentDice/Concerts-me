"""
Playlist builder module.
Orchestrates the process of fetching setlists and creating Spotify playlists.
"""

from setlistfm_api import get_setlist_for_event
from spotify_client import SpotifyClient


def process_events(events, dry_run=False):
    """
    Process all events and create/update Spotify playlists.
    
    Args:
        events: List of event dictionaries
        dry_run: If True, don't actually create playlists
    """
    # Initialize Spotify client
    spotify = SpotifyClient(dry_run=dry_run)
    
    # Statistics
    stats = {
        "total_events": len(events),
        "playlists_created": 0,
        "playlists_updated": 0,
        "festivals_processed": 0,
        "total_songs_matched": 0,
        "total_failed_matches": 0,
        "events_skipped": 0,
        "skipped_reasons": [],
        "failed_songs": []
    }
    
    for idx, event in enumerate(events, 1):
        print(f"\n[INFO] ========== Processing event {idx}/{len(events)} ==========")
        print(f"[INFO] Artist: {event['artist']}")
        print(f"[INFO] Date: {event['date']}")
        print(f"[INFO] Venue: {event.get('venue', 'N/A')}")
        print(f"[INFO] City: {event.get('city', 'N/A')}")
        print(f"[INFO] Festival: {event.get('is_festival', False)}")
        
        # Get setlist from Setlist.fm
        setlist_data = get_setlist_for_event(event)
        
        if not setlist_data:
            reason = f"{event['artist']} on {event['date']}: No setlist data found"
            print(f"[WARN] {reason}")
            stats["events_skipped"] += 1
            stats["skipped_reasons"].append(reason)
            continue
        
        # Extract songs from setlist
        all_songs = []
        artists_in_order = []
        
        # Add openers first (if any)
        for opener in setlist_data.get("openers", []):
            opener_name = opener["name"]
            opener_songs = opener["songs"]
            
            if opener_songs:
                print(f"[INFO] Adding {len(opener_songs)} songs from opener: {opener_name}")
                artists_in_order.append(opener_name)
                all_songs.extend([{"name": song, "artist": opener_name} for song in opener_songs])
        
        # Add headliner
        headliner = setlist_data.get("headliner", {})
        headliner_name = headliner.get("name", "")
        headliner_songs = headliner.get("songs", [])
        
        if headliner_songs:
            print(f"[INFO] Adding {len(headliner_songs)} songs from headliner: {headliner_name}")
            artists_in_order.append(headliner_name)
            all_songs.extend([{"name": song, "artist": headliner_name} for song in headliner_songs])
        
        if not all_songs:
            reason = f"{event['artist']} on {event['date']}: No songs found in setlist"
            print(f"[WARN] {reason}")
            stats["events_skipped"] += 1
            stats["skipped_reasons"].append(reason)
            continue
        
        print(f"[INFO] Total songs to match: {len(all_songs)}")
        
        # Match songs to Spotify tracks
        track_uris = []
        matched_count = 0
        failed_count = 0
        
        for song_info in all_songs:
            song_name = song_info["name"]
            artist_name = song_info["artist"]
            
            track_uri = spotify.search_track(song_name, artist_name)
            
            if track_uri:
                track_uris.append(track_uri)
                matched_count += 1
            else:
                failed_count += 1
                failed_song = f"{song_name} by {artist_name} ({event['artist']} - {event['date']})"
                stats["failed_songs"].append(failed_song)
                print(f"[WARN] Failed to match: {song_name} by {artist_name}")
        
        stats["total_songs_matched"] += matched_count
        stats["total_failed_matches"] += failed_count
        
        print(f"[INFO] Matched {matched_count}/{len(all_songs)} songs ({failed_count} failed)")
        
        if not track_uris:
            reason = f"{event['artist']} on {event['date']}: No tracks matched on Spotify"
            print(f"[WARN] {reason}")
            stats["events_skipped"] += 1
            stats["skipped_reasons"].append(reason)
            continue
        
        # Generate playlist name and description
        if setlist_data.get("is_festival"):
            # Festival mode
            festival_name = setlist_data.get("festival_name", event["artist"])
            playlist_name = f"{festival_name} - {event['date']}"
            description = f"{event['date']} - {event.get('city', '')}"
            
            stats["festivals_processed"] += 1
        else:
            # Normal concert mode
            playlist_name = f"{event['artist']} - {event['date']}"
            description = f"{event['date']} - {event.get('venue', '')} - {event.get('city', '')}"
        
        # Create or update playlist
        playlist_name_trimmed = playlist_name[:100] if len(playlist_name) > 100 else playlist_name
        existing_id = spotify.find_playlist_by_name(playlist_name_trimmed)
        
        if existing_id:
            print(f"[INFO] Playlist '{playlist_name_trimmed}' already exists (ID: {existing_id})")
            
            if dry_run:
                print(f"[DRY RUN] Would update existing playlist with {len(track_uris)} tracks")
            else:
                print(f"[INFO] Updating existing playlist with {len(track_uris)} tracks")
                spotify.update_playlist(existing_id, track_uris)
            
            stats["playlists_updated"] += 1
        else:
            print(f"[INFO] Creating new playlist: {playlist_name_trimmed}")
            
            if dry_run:
                print(f"[DRY RUN] Would create playlist with {len(track_uris)} tracks")
                stats["playlists_created"] += 1
            else:
                playlist_id = spotify.create_playlist(playlist_name_trimmed, description, track_uris)
                
                if playlist_id:
                    stats["playlists_created"] += 1
                else:
                    print(f"[ERROR] Failed to create playlist for {event['artist']}")
    
    # Print summary report
    print("\n" + "="*60)
    print("PLAYLIST GENERATION SUMMARY")
    print("="*60)
    print(f"Total events processed: {stats['total_events']}")
    print(f"Playlists created: {stats['playlists_created']}")
    print(f"Playlists updated: {stats['playlists_updated']}")
    print(f"Festivals processed: {stats['festivals_processed']}")
    print(f"Events skipped: {stats['events_skipped']}")
    print(f"Total songs matched: {stats['total_songs_matched']}")
    print(f"Total failed matches: {stats['total_failed_matches']}")
    
    if stats["total_songs_matched"] + stats["total_failed_matches"] > 0:
        success_rate = (stats["total_songs_matched"] / 
                       (stats["total_songs_matched"] + stats["total_failed_matches"])) * 100
        print(f"Match success rate: {success_rate:.1f}%")
    
    # Print details of skipped events
    if stats["skipped_reasons"]:
        print("\n" + "-"*60)
        print("SKIPPED EVENTS:")
        print("-"*60)
        for reason in stats["skipped_reasons"]:
            print(f"  - {reason}")
    
    # Print details of failed song matches
    if stats["failed_songs"]:
        print("\n" + "-"*60)
        print("FAILED SONG MATCHES:")
        print("-"*60)
        for song in stats["failed_songs"]:
            print(f"  - {song}")
    
    print("="*60)
