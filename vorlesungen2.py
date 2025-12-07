import streamlit as st
import pdfplumber
import re
from datetime import datetime, timedelta
from ics import Calendar, Event
import pytz

# --- PAGE CONFIG ---
st.set_page_config(page_title="Vorlesungs-Planer", page_icon="📅", layout="centered")

# --- HELPER FUNCTIONS ---

def clean_title_logic(course_id, raw_title):
    """
    Regeln:
    - Wenn ID im Format 'X.Y' (2 Ziffern, 1 Punkt) -> Wird nach Bindestrich abgeschnitten (Komplettes Modul).
    - Wenn ID im Format 'X.Y.Z' (3 Ziffern, 2 Punkte) -> Vollen Titel behalten (spezifische Lehrveranstaltung).
    """
    # Count dots to determine level
    dots = course_id.count('.')
    
    if dots == 1: # z.B. "1.3"
        if "-" in raw_title:
            return raw_title.split("-")[0].strip()
    
    # Für "1.3.1" ganzen Titel ausgeben
    return raw_title.strip()

def extract_events(text):
    events = []
    lines = text.split('\n')
    
    # Matches "1.3" or "1.3.1" followed by text
    id_pattern = re.compile(r'^(\d+(\.\d+)+)\s+(.*)')
    current_block_lines = []
    
    for line in lines:
        match = id_pattern.match(line)
        if match:
            if current_block_lines:
                events.extend(process_event_block(current_block_lines))
            current_block_lines = [line]
        elif current_block_lines:
            current_block_lines.append(line)
    
    if current_block_lines:
        events.extend(process_event_block(current_block_lines))

    return events

def process_event_block(block_lines):
    block_events = []
    # Parse the header line again to get ID and Title
    id_match = re.match(r'^(\d+(\.\d+)+)\s+(.*)', block_lines[0])
    if not id_match:
        return []

    course_id, _, raw_title = id_match.groups()
    
    # Apply the specific cleaning logic requested
    final_title = clean_title_logic(course_id, raw_title)
    
    block_text = "\n".join(block_lines)
    default_loc_match = re.search(r'(Raum|Aula|Hörsaal)\s+([\w\.]+)', block_text)
    default_location = default_loc_match.group(0) if default_loc_match else "TBA"

    for line in block_lines:
        # Regex for Day, optional Date, Time-Range
        time_match = re.search(
            r'(Mo|Di|Mi|Do|Fr|Sa|So)\.?\s+'
            r'(?:(\d{2}\.\d{2}\.\d{2,4})\s+)?' 
            r'(\d{2}[.:]\d{2})\s*-\s*(\d{2}[.:]\d{2})',
            line
        )
        if time_match:
            weekday, date_str, start_time, end_time = time_match.groups()
            
            # Normalize Year
            if date_str:
                parts = date_str.split('.')
                if len(parts[2]) == 2:
                    date_str = f"{parts[0]}.{parts[1]}.20{parts[2]}"

            line_loc_match = re.search(r'(Raum|Aula|Hörsaal)\s+([\w\.]+)', line)
            location = line_loc_match.group(0) if line_loc_match else default_location

            block_events.append({
                'id': course_id,
                'title': final_title,
                # Unique identifier for grouping in UI
                'full_label': f"{course_id} {final_title}", 
                'type': 'single' if date_str else 'recurring',
                'date': date_str,
                'weekday': weekday,
                'start_time': start_time.replace('.', ':'),
                'end_time': end_time.replace('.', ':'),
                'location': location
            })
    return block_events

def detect_holiday_weeks(events, semester_start, semester_end):
    """Identifies weeks within the semester range where NO 'single' events occur."""
    active_weeks = set()
    single_events_found = False
    
    for e in events:
        if e['type'] == 'single' and e['date']:
            single_events_found = True
            try:
                dt = datetime.strptime(e['date'], '%d.%m.%Y')
                active_weeks.add(dt.isocalendar()[1])
            except ValueError:
                pass
    
    if not single_events_found:
        return []

    holiday_weeks = []
    current = semester_start
    while current <= semester_end:
        wk = current.isocalendar()[1]
        if wk not in active_weeks:
            holiday_weeks.append(wk)
        current += timedelta(days=7)
    
    return sorted(list(set(holiday_weeks)))

def generate_ics(selected_events, semester_start, semester_end, holiday_weeks):
    c = Calendar()
    tz = pytz.timezone("Europe/Berlin")
    weekday_map = {"Mo": 0, "Di": 1, "Mi": 2, "Do": 3, "Fr": 4, "Sa": 5, "So": 6}
    
    start_dt_global = datetime.combine(semester_start, datetime.min.time())
    end_dt_global = datetime.combine(semester_end, datetime.min.time())

    for event in selected_events:
        try:
            if event['type'] == 'single':
                start_dt = datetime.strptime(f"{event['date']} {event['start_time']}", '%d.%m.%Y %H:%M')
                end_dt = datetime.strptime(f"{event['date']} {event['end_time']}", '%d.%m.%Y %H:%M')
                
                e = Event()
                e.name = event['full_label']
                e.location = event['location']
                e.begin = tz.localize(start_dt)
                e.end = tz.localize(end_dt)
                c.events.add(e)

            elif event['type'] == 'recurring':
                target_weekday = weekday_map[event['weekday']]
                current = start_dt_global
                
                # Advance to first occurrence
                while current.weekday() != target_weekday:
                    current += timedelta(days=1)
                
                while current <= end_dt_global:
                    # Skip Holidays
                    if current.isocalendar()[1] in holiday_weeks:
                        current += timedelta(days=7)
                        continue

                    start_str = f"{current.strftime('%d.%m.%Y')} {event['start_time']}"
                    end_str = f"{current.strftime('%d.%m.%Y')} {event['end_time']}"
                    
                    e = Event()
                    e.name = event['full_label']
                    e.location = event['location']
                    e.begin = tz.localize(datetime.strptime(start_str, '%d.%m.%Y %H:%M'))
                    e.end = tz.localize(datetime.strptime(end_str, '%d.%m.%Y %H:%M'))
                    c.events.add(e)
                    current += timedelta(days=7)

        except Exception:
            continue

    return c.serialize()


# --- MAIN UI ---

st.title("Vorlesungs-Planer")

# 1. INITIALIZE DATES (Default to today if nothing loaded)
if 'sem_start_date' not in st.session_state:
    st.session_state['sem_start_date'] = datetime.now()
if 'sem_end_date' not in st.session_state:
    st.session_state['sem_end_date'] = datetime.now() + timedelta(weeks=16)

# 2. SIDEBAR & PARSING LOGIC
with st.sidebar:
    st.header("Einstellungen")
    uploaded_file = st.file_uploader("Vorlesungsverzeichnis (PDF) hochladen", type="pdf")
    
    # --- Parse PDF *HERE* before rendering date widgets ---
    if uploaded_file:
        # Only parse if it's a new file
        if st.session_state.get('last_file') != uploaded_file.name:
            with st.spinner("Analysiere PDF..."):
                text = ""
                with pdfplumber.open(uploaded_file) as pdf:
                    for page in pdf.pages: text += page.extract_text() + "\n"
                
                events = extract_events(text)
                st.session_state['all_events'] = events
                st.session_state['last_file'] = uploaded_file.name
                
                # AUTO-DETECT START & END FROM EVENTS
                found_dates = []
                for e in events:
                    if e['date']:
                        try:
                            dt = datetime.strptime(e['date'], '%d.%m.%Y')
                            found_dates.append(dt)
                        except ValueError: pass
                
                if found_dates:
                    st.session_state['sem_start_date'] = min(found_dates)
                    st.session_state['sem_end_date'] = max(found_dates)

    # --- Now render the widgets (they will read the updated session state) ---
    sem_start = st.date_input("Semester Start", key='sem_start_date')
    sem_end = st.date_input("Semester Ende", key='sem_end_date')

if not uploaded_file:
    st.info("← Bitte PDF hochladen.")

# Load data for the rest of the app
all_events = st.session_state['all_events']
holidays = detect_holiday_weeks(all_events, sem_start, sem_end)

# 3. SEARCH & SELECT
st.subheader("Module oder Vorlesungen auswählen")
query = st.text_input("Modulnummern eingeben (z.B. '1.3, 4.2', oder einzelne Vorlesungen, z.B. '1.3.3')", placeholder="Mehrere Einträge mit Komma trennen...")

final_events_to_process = []

if query:
    search_ids = [s.strip() for s in query.split(',') if s.strip()]
    
    # Filter events matching IDs
    matched_events = []
    for s_id in search_ids:
        # Matches "1.3", "1.3.1", "1.3.2" etc.
        matched_events.extend([e for e in all_events if e['id'].startswith(s_id)])
    
    if not matched_events:
        st.warning("Keine Module gefunden.")
    else:
        # GROUPING LOGIC: ID -> Title -> Events
        # We group by ID first (e.g. 1.3.1)
        grouped_by_id = {}
        for e in matched_events:
            if e['id'] not in grouped_by_id:
                grouped_by_id[e['id']] = {}
            
            # Group by Title within ID (e.g. "Seminar A", "Seminar B")
            title = e['title']
            if title not in grouped_by_id[e['id']]:
                grouped_by_id[e['id']][title] = []
            grouped_by_id[e['id']][title].append(e)

        st.divider()
        st.write("Bitte Auswahl treffen:")

        # RENDER UI
        # Sort IDs to show 1.3.1 before 1.3.2
        sorted_ids = sorted(grouped_by_id.keys())

        for eid in sorted_ids:
            title_groups = grouped_by_id[eid]
            titles = sorted(title_groups.keys())
            
            # Case A: Only 1 Title for this ID (Most common)
            # e.g., "1.3.1 Vorlesung" -> Show Checkbox (Default True)
            if len(titles) == 1:
                t = titles[0]
                # Using a container for layout
                c1, c2 = st.columns([0.1, 0.9])
                # Unique key is crucial for Streamlit
                if c1.checkbox("", value=True, key=f"chk_{eid}_{t}"):
                    final_events_to_process.extend(title_groups[t])
                    c2.markdown(f"**{eid} {t}**")
                else:
                    c2.markdown(f"~~{eid} {t}~~")

            # Case B: Multiple Titles for this ID (Groups/Seminars)
            # e.g. "1.3.2 Seminar A", "1.3.2 Seminar B" -> Show Multiselect or List of Checkboxes
            else:
                st.markdown(f"**{eid} - Bitte Gruppe wählen:**")
                for t in titles:
                    c1, c2 = st.columns([0.1, 0.9])
                    # Default False ensures they actively pick a group
                    if c1.checkbox("", value=False, key=f"chk_{eid}_{t}"):
                        final_events_to_process.extend(title_groups[t])
                        c2.write(f"{t}")
                    else:
                        c2.write(f"{t}")
                st.markdown("---")

# 4. PREVIEW & DOWNLOAD
if final_events_to_process:
    st.divider()
    st.subheader("Vorschau")

    # --- FIX START: Deduplicate Events ---
    # We create a unique signature for every event (ID + Title + Day + Time)
    # This removes duplicates even if the parser found the same line twice.
    unique_events = []
    seen_signatures = set()
    
    for e in final_events_to_process:
        # Create a signature string
        sig = f"{e['id']}|{e['title']}|{e['weekday']}|{e['start_time']}|{e['date']}"
        
        if sig not in seen_signatures:
            seen_signatures.add(sig)
            unique_events.append(e)
            
    final_events_to_process = unique_events
    # --- FIX END ---

    # ... existing preview code continues below ...
    
    # Simple list of Unique Titles (as requested)
    unique_titles = sorted(list(set([e['full_label'] for e in final_events_to_process])))
    for item in unique_titles:
        st.text(f"• {item}")

    ics_data = generate_ics(final_events_to_process, sem_start, sem_end, holidays)
    
    st.download_button(
        label="Download .ics Datei",
        data=ics_data,
        file_name="Vorlesungen.ics",
        mime="text/calendar",
        type="primary"
    )
