"""IFA (Israel Football Association) HTML DOM parser using selectolax Lexbor engine."""

import re
from datetime import date
from typing import Any, Optional

try:
    from selectolax.lexbor import LexborHTMLParser
except ImportError:
    # Fallback to standard parser if lexbor backend not compiled
    from selectolax.parser import HTMLParser as LexborHTMLParser  # type: ignore

from src.schemas import (
    MatchEvent,
    MatchMetadata,
    PlayerAppearance,
    PlayerProfile,
    parse_hebrew_date,
    parse_hebrew_minute,
    parse_shirt_number,
)


class IFAParser:
    """Production-grade parser for football.org.il match and player pages."""

    @staticmethod
    def parse_game(
        html: str,
        match_id: Optional[int] = None,
        season_id: Optional[int] = None,
        league_id: Optional[int] = None,
    ) -> tuple[MatchMetadata, list[PlayerAppearance], list[MatchEvent]]:
        """Parse an IFA game details HTML page into metadata, player appearances, and match events."""
        tree = LexborHTMLParser(html)

        # 1. Extract match ID
        if match_id is None:
            sec = tree.css_first("section.game-details")
            if sec and sec.attributes.get("id"):
                id_attr = sec.attributes.get("id", "")
                m = re.search(r"\d+", id_attr)
                if m:
                    match_id = int(m.group(0))
        if match_id is None:
            # Fallback search anywhere in HTML
            m = re.search(r"game_id=(\d+)|gfd_(\d+)", html)
            if m:
                match_id = int(m.group(1) or m.group(2))
            else:
                match_id = 0

        # 2. Extract Team Names
        home_el = tree.css_first(".team-home")
        guest_el = tree.css_first(".team-guest")

        if home_el:
            goalers = home_el.css_first(".goalers")
            home_text = home_el.text(strip=True)
            if goalers:
                home_name = home_text.replace(goalers.text(strip=True), "").strip()
            else:
                home_name = home_text
        else:
            home_name = ""

        if guest_el:
            goalers = guest_el.css_first(".goalers")
            guest_text = guest_el.text(strip=True)
            if goalers:
                guest_name = guest_text.replace(goalers.text(strip=True), "").strip()
            else:
                guest_name = guest_text
        else:
            guest_name = ""

        # 3. Extract Date, Time, Scores, Stadium
        match_date: Optional[date] = None
        match_time: Optional[str] = None
        home_score: Optional[int] = None
        away_score: Optional[int] = None
        ht_home: Optional[int] = None
        ht_away: Optional[int] = None
        stadium: Optional[str] = None

        game_time_el = tree.css_first(".game-time")
        if game_time_el:
            # Date and Time from .date
            date_el = game_time_el.css_first(".date")
            if date_el:
                dt_text = date_el.text(strip=True)
                match_date = parse_hebrew_date(dt_text)
                tm = re.search(r"(\d{1,2}:\d{2})", dt_text)
                if tm:
                    match_time = tm.group(1)

            # Scores from .total
            total_el = game_time_el.css_first(".total")
            if total_el:
                # Strip sr-only elements before extracting digits
                cleaned_total = re.sub(r"<[^>]+>", "", re.sub(r"<i[^>]*sr-only[^>]*>.*?</i>", "", total_el.html))
                sm = re.search(r"(\d+)\s*:\s*(\d+)", cleaned_total)
                if sm:
                    home_score = int(sm.group(1))
                    away_score = int(sm.group(2))

            # Half time scores from .result-half
            half_el = game_time_el.css_first(".result-half")
            if half_el:
                cleaned_half = re.sub(r"<[^>]+>", "", re.sub(r"<i[^>]*sr-only[^>]*>.*?</i>", "", half_el.html))
                hsm = re.search(r"(\d+)\s*:\s*(\d+)", cleaned_half)
                if hsm:
                    ht_home = int(hsm.group(1))
                    ht_away = int(hsm.group(2))

            # Stadium from .place
            place_el = game_time_el.css_first(".place")
            if place_el:
                stadium = place_el.text(strip=True)

        # 4. Referee
        referee: Optional[str] = None
        judge_el = tree.css_first(".judge")
        if judge_el:
            # Look for head referee link/text
            for a in judge_el.css("a"):
                txt = a.text(strip=True)
                if "שופט ראשי" in txt:
                    referee = txt.replace("שופט ראשי", "").strip()
                    break
            if not referee:
                # Search for pattern inside judge text
                jm = re.search(r"([^\n\r]+?)\s*שופט ראשי", judge_el.text(strip=True))
                if jm:
                    referee = jm.group(1).strip()

        metadata = MatchMetadata(
            match_id=match_id,
            season_id=season_id,
            league_id=league_id,
            match_date=match_date,
            match_time=match_time,
            home_team_name=home_name,
            away_team_name=guest_name,
            home_score=home_score,
            away_score=away_score,
            home_half_time_score=ht_home,
            away_half_time_score=ht_away,
            stadium=stadium,
            referee=referee,
        )

        # 5. Extract Player Appearances
        appearances: list[PlayerAppearance] = []
        player_lookup: dict[tuple[str, Optional[int]], int] = {}  # (side, shirt_num) -> player_id
        name_lookup: dict[tuple[str, str], int] = {}  # (side, clean_name) -> player_id

        pc = tree.css_first(".players-cont")
        if pc:
            current_section = ""
            for child in pc.iter():
                if child.parent != pc:
                    continue
                cls = child.attributes.get("class", "")
                if "title" in cls:
                    current_section = child.text(strip=True)
                elif "home" in cls or "guest" in cls:
                    side = "home" if "home" in cls else "away"
                    current_team_name = home_name if side == "home" else guest_name
                    is_starter = "הרכב פותח" in current_section
                    is_bench = "לא שותפו" in current_section

                    for a in child.css("a[href*='player_id']"):
                        href = a.attributes.get("href", "")
                        pid_m = re.search(r"player_id=(\d+)", href)
                        if not pid_m:
                            continue
                        p_id = int(pid_m.group(1))

                        p = a.css_first(".player")
                        if not p:
                            continue

                        num_el = p.css_first(".number")
                        name_el = p.css_first(".name")
                        moves_el = p.css_first(".moves")

                        shirt_num = parse_shirt_number(num_el.text(strip=True) if num_el else None)

                        raw_name = name_el.text(strip=True) if name_el else ""
                        is_gk = "(GK)" in raw_name
                        is_c = "(C)" in raw_name
                        clean_name = re.sub(r"-\s*\((GK|C)\)", "", raw_name).strip()

                        moves_text = moves_el.text(strip=True) if moves_el else ""

                        sub_in_m = re.search(r"נכנס\s*(?:דקה)?\s*(\d+)", moves_text)
                        sub_out_m = re.search(r"יצא\s*(?:דקה)?\s*(\d+)", moves_text)
                        yellow_m = re.search(r"כרטיס\s*צהוב\s*(?:דקה)?\s*(\d+)", moves_text)
                        red_m = re.search(r"כרטיס\s*אדום\s*(?:דקה)?\s*(\d+)", moves_text)
                        goal_matches = re.findall(r"שער\s*(?:דקה)?\s*(\d+)", moves_text)

                        sub_in = int(sub_in_m.group(1)) if sub_in_m else None
                        sub_out = int(sub_out_m.group(1)) if sub_out_m else None
                        yellow = int(yellow_m.group(1)) if yellow_m else None
                        red = int(red_m.group(1)) if red_m else None
                        goals = [int(g) for g in goal_matches]

                        # Calculate exact minutes played
                        if is_bench:
                            minutes = 0
                        elif is_starter:
                            end_min = red if red else (sub_out if sub_out else 90)
                            minutes = end_min
                        else:
                            start_min = sub_in if sub_in is not None else 0
                            end_min = red if red else (sub_out if sub_out else 90)
                            minutes = max(0, end_min - start_min)

                        app = PlayerAppearance(
                            match_id=match_id,
                            player_id=p_id,
                            team_side=side,
                            team_name=current_team_name,
                            player_name=clean_name,
                            shirt_number=shirt_num,
                            is_starter=is_starter,
                            is_captain=is_c,
                            is_goalkeeper=is_gk,
                            sub_in_minute=sub_in,
                            sub_out_minute=sub_out,
                            yellow_card_minute=yellow,
                            red_card_minute=red,
                            goals_scored=len(goals),
                            goal_minutes=goals,
                            minutes_played=minutes,
                            season_id=season_id,
                            league_id=league_id,
                        )
                        appearances.append(app)
                        if shirt_num is not None:
                            player_lookup[(side, shirt_num)] = p_id
                        name_lookup[(side, clean_name)] = p_id

        # 6. Extract Match Events from Timeline Nodes
        events: list[MatchEvent] = []
        event_seq = 0
        nodes = tree.css(".node")

        for n in nodes:
            cls = n.attributes.get("class", "")

            # Determine minute
            min_el = n.css_first(".minute div")
            if min_el and min_el.text(strip=True).isdigit():
                ev_minute = int(min_el.text(strip=True))
            else:
                num_span = n.css_first("span:not(.sr-only)")
                if num_span and num_span.text(strip=True).isdigit():
                    ev_minute = int(num_span.text(strip=True))
                else:
                    ev_minute = 0

            # Determine team side
            side: Optional[str] = None
            if "team1" in cls:
                side = "home"
            elif "team2" in cls:
                side = "away"

            current_team_name = home_name if side == "home" else (guest_name if side == "away" else None)

            if "goal" in cls:
                event_seq += 1
                text_el = n.css_first(".text")
                text = text_el.text(strip=True) if text_el else n.text(strip=True)
                p_m = re.search(r"([^(]+)\s*\((\d+)\)", text)
                p_name = p_m.group(1).strip() if p_m else None
                s_num = int(p_m.group(2)) if p_m else None
                p_id = player_lookup.get((side or "", s_num)) if (side and s_num) else None
                if not p_id and side and p_name:
                    p_id = name_lookup.get((side, p_name))

                ev = MatchEvent(
                    match_id=match_id,
                    event_seq=event_seq,
                    event_type="goal",
                    minute=ev_minute,
                    team_side=side,
                    team_name=current_team_name,
                    player_id=p_id,
                    player_name=p_name,
                    shirt_number=s_num,
                    description=f"Goal by {p_name} ({s_num}) at minute {ev_minute}",
                )
                events.append(ev)

            elif "yellow" in cls:
                event_seq += 1
                text_el = n.css_first(".text")
                text = text_el.text(strip=True) if text_el else n.text(strip=True)
                p_m = re.search(r"([^(]+)\s*\((\d+)\)", text)
                p_name = p_m.group(1).strip() if p_m else None
                s_num = int(p_m.group(2)) if p_m else None
                p_id = player_lookup.get((side or "", s_num)) if (side and s_num) else None
                if not p_id and side and p_name:
                    p_id = name_lookup.get((side, p_name))

                ev = MatchEvent(
                    match_id=match_id,
                    event_seq=event_seq,
                    event_type="yellow_card",
                    minute=ev_minute,
                    team_side=side,
                    team_name=current_team_name,
                    player_id=p_id,
                    player_name=p_name,
                    shirt_number=s_num,
                    description=f"Yellow card to {p_name} ({s_num}) at minute {ev_minute}",
                )
                events.append(ev)

            elif "red" in cls and "card" in cls:
                event_seq += 1
                text_el = n.css_first(".text")
                text = text_el.text(strip=True) if text_el else n.text(strip=True)
                p_m = re.search(r"([^(]+)\s*\((\d+)\)", text)
                p_name = p_m.group(1).strip() if p_m else None
                s_num = int(p_m.group(2)) if p_m else None
                p_id = player_lookup.get((side or "", s_num)) if (side and s_num) else None
                if not p_id and side and p_name:
                    p_id = name_lookup.get((side, p_name))

                ev = MatchEvent(
                    match_id=match_id,
                    event_seq=event_seq,
                    event_type="red_card",
                    minute=ev_minute,
                    team_side=side,
                    team_name=current_team_name,
                    player_id=p_id,
                    player_name=p_name,
                    shirt_number=s_num,
                    description=f"Red card to {p_name} ({s_num}) at minute {ev_minute}",
                )
                events.append(ev)

            elif "change" in cls or "playerout" in cls:
                outs = n.css(".player.PlayerOut")
                ins = n.css(".player.PlayerIn")
                for p_out, p_in in zip(outs, ins):
                    event_seq += 1
                    out_txt = p_out.text(strip=True).replace("יצא", "").strip()
                    in_txt = p_in.text(strip=True).replace("נכנס", "").strip()

                    out_m = re.search(r"([^(]+)\s*\((\d+)\)", out_txt)
                    in_m = re.search(r"([^(]+)\s*\((\d+)\)", in_txt)

                    out_name = out_m.group(1).strip() if out_m else out_txt
                    out_num = int(out_m.group(2)) if out_m else None
                    in_name = in_m.group(1).strip() if in_m else in_txt
                    in_num = int(in_m.group(2)) if in_m else None

                    out_id = player_lookup.get((side or "", out_num)) if (side and out_num) else None
                    in_id = player_lookup.get((side or "", in_num)) if (side and in_num) else None

                    ev = MatchEvent(
                        match_id=match_id,
                        event_seq=event_seq,
                        event_type="substitution",
                        minute=ev_minute,
                        team_side=side,
                        team_name=current_team_name,
                        player_id=out_id,
                        player_name=out_name,
                        shirt_number=out_num,
                        sub_in_player_id=in_id,
                        sub_in_player_name=in_name,
                        sub_in_shirt_number=in_num,
                        description=f"Substitution: {in_name} IN, {out_name} OUT at minute {ev_minute}",
                    )
                    events.append(ev)

            elif "number" in cls:
                txt = n.text(strip=True)
                if any(k in txt for k in ["תחילת", "סיום", "מחצית"]):
                    event_seq += 1
                    ev = MatchEvent(
                        match_id=match_id,
                        event_seq=event_seq,
                        event_type="period",
                        minute=ev_minute,
                        team_side=None,
                        team_name=None,
                        player_id=None,
                        player_name=None,
                        shirt_number=None,
                        description=txt,
                    )
                    events.append(ev)

        return metadata, appearances, events

    @staticmethod
    def parse_player(
        html: str,
        player_id: Optional[int] = None,
        season_id: Optional[int] = None,
    ) -> PlayerProfile:
        """Parse an IFA player profile page into demographic and season details."""
        tree = LexborHTMLParser(html)

        # 1. Player ID
        if player_id is None:
            # Check script or URL links
            m = re.search(r"player_id=(\d+)", html)
            if m:
                player_id = int(m.group(1))
            else:
                player_id = 0

        # 2. Player Name
        title_el = tree.css_first(".new-player-card_title")
        full_name = title_el.text(strip=True) if title_el else ""

        # 3. Birth Date & Nationality
        birth_date: Optional[date] = None
        nationality = "ישראל"

        for li in tree.css(".new-player-card_data-list li"):
            txt = li.text(strip=True)
            if "תאריך לידה" in txt:
                raw_val = txt.replace("תאריך לידה:", "").strip()
                birth_date = parse_hebrew_date(raw_val)
            elif "אזרחות" in txt:
                nationality = txt.replace("אזרחות:", "").strip()

        # 4. Current Club & League
        team_el = tree.css_first(".new-player-data_title")
        team_text = team_el.text(strip=True) if team_el else ""
        team_m = re.search(r"נתוני השחקן בקבוצה:\s*(.*?)(?:\s*\((.*?)\))?$", team_text)
        current_team = team_m.group(1).strip() if team_m else None
        current_league = team_m.group(2).strip() if team_m and team_m.group(2) else None

        # 5. Summary Stats (Goals, Cards)
        stats: dict[str, Any] = {}
        for row in tree.css(".table_view.club_table .table_row, .new-player-data_row .table_row"):
            txt = row.text(strip=True)
            if "מסגרתליגה" in txt:
                gm = re.search(r"שערים\s*(\d+)", txt)
                if gm:
                    stats["league_goals"] = int(gm.group(1))
            elif "מסגרתגביע המדינה" in txt:
                gm = re.search(r"שערים\s*(\d+)", txt)
                if gm:
                    stats["state_cup_goals"] = int(gm.group(1))
            elif "מסגרתהכל" in txt:
                gm = re.search(r"שערים\s*(\d+)", txt)
                if gm:
                    stats["total_goals"] = int(gm.group(1))
            elif "צהוב - ליגה" in txt:
                cm = re.search(r"מס'?\s*כרטיסים\s*(\d+)", txt)
                if cm:
                    stats["yellow_cards"] = int(cm.group(1))
            elif "סוג כרטיס:אדום" in txt:
                cm = re.search(r"מס'?\s*כרטיסים\s*(\d+)", txt)
                if cm:
                    stats["red_cards"] = int(cm.group(1))

        return PlayerProfile(
            player_id=player_id,
            full_name=full_name,
            birth_date=birth_date,
            nationality=nationality,
            current_team_name=current_team,
            current_league_name=current_league,
            season_id=season_id,
            stats=stats if stats else None,
        )


def parse_game_page(
    html: str,
    match_id: Optional[int] = None,
    season_id: Optional[int] = None,
    league_id: Optional[int] = None,
) -> tuple[MatchMetadata, list[PlayerAppearance], list[MatchEvent]]:
    """Helper functional wrapper for IFAParser.parse_game."""
    return IFAParser.parse_game(html, match_id=match_id, season_id=season_id, league_id=league_id)


def parse_player_page(
    html: str,
    player_id: Optional[int] = None,
    season_id: Optional[int] = None,
) -> PlayerProfile:
    """Helper functional wrapper for IFAParser.parse_player."""
    return IFAParser.parse_player(html, player_id=player_id, season_id=season_id)
