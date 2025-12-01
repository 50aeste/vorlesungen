import streamlit as st
import pdfplumber
import re
from datetime import datetime, timedelta
from ics import Calendar, Event
import pytz
from io import BytesIO

# --- PAGE CONFIG ---
st.set_page_config(page_title="Uni-Planer", page_icon="📅", layout="wide")

# --- HELPER FUNCTIONS ---

def clean_title_string(title):
    """Cuts off title at the first dash (-) to keep it short."""
    if "-" in title:
        return title.split("-")[0].strip()
    return title.strip()

def parse_pdf(file):
    """Extracts text from uploaded PDF file object."""
    with pdfplumber.open(file) as pdf:
        full_text = ""
        for page in pdf.pages:
            full_text += page.extract_text() + "\n"
    return full_text

def extract_events(text):
    """
    Parses the text and returns a list of event dictionaries.
    Logic handles blocks of text associated with an ID.
    """
    events = []
    lines = text.split('\n')
    
    # Pattern: "1.2.3  Title of Course"
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
    id_match = re.match(r'^(\d+(\.\d+)+)\s+(.*)', block_lines[0])
    if not id_match:
        return []

    course_id, _, raw_title = id_match.groups()
    clean_title = clean_title_string(raw_title)
    
    block_text = "\n".join(block_lines)
    # Default location if mentioned once in the block
    default_loc_match = re.search(r'(Raum|Aula|Hörsaal)\s+([\w\.]+)', block_text)
    default_location = default_loc_match.group(0) if default_loc_match else "TBA"

    for line in block_lines:
        # Regex for Day, optional Date, Time-Range
        # Matches: "Di 14:00-16:00" or "Fr 12.01.24 10:00-12:00"
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

            # Line specific location?
            line_loc_match = re.search(r'(Raum|Aula|Hörsaal)\s+([\w\.]+)', line)
            location = line_loc_match.group(0) if line_loc_match else default_location

            # Create a unique signature for grouping later
            # Signature = Weekday + StartTime + EndTime + Location
            signature = f"{weekday} {start_time}-{end_time} @ {location}"

            block_events.append({
                'id': course_id,
                'title': clean_title,
                'full_label': f"{course_id} {clean_title}", 
                'type': 'single' if date_str else 'recurring',
                'date': date_str,
                'weekday': weekday,
                'start_time': start_time.replace('.', ':'),
                'end_time': end_time.replace('.', ':'),
                'location': location,
                'signature': signature
            })
    return block_events

def detect_holiday_weeks(events, semester_start, semester_end):
    """
    Identifies weeks within the semester range where NO 'single' events occur.
    Assumption: If the PDF lists specific dates (single events), gaps indicate holidays.
    """
    # 1. Collect all weeks that contain at least one single event
    active_weeks = set()
    single_events_found = False
    
    for e in events:
        if e['type'] == 'single' and e['date']:
            single_events_found = True
            try:
                dt = datetime.strptime(e['date'], '%d.%m.%Y')
                # Use ISO week number
                active_weeks.add(dt.isocalendar()[1])
            except ValueError:
                pass
    
    if not single_events_found:
        return [] # Cannot detect holidays if no dates are in PDF

    # 2. Iterate through semester range and find missing weeks
    holiday_weeks = []
    current = semester_start
    while current <= semester_end:
        wk = current.isocalendar()[1]
        if wk not in active_weeks:
            holiday_weeks.append(wk)
        current += timedelta(days=7)
    
    # Deduplicate and sort
    return sorted(list(set(holiday_weeks)))

def generate_ics(selected_variants, semester_start, semester_end, holiday_weeks):
    c = Calendar()
    tz = pytz.timezone("Europe/Berlin")
    weekday_map = {"Mo": 0, "Di": 1, "Mi": 2, "Do": 3, "Fr": 4, "Sa": 5, "So": 6}
    
    start_dt_global = datetime.combine(semester_start, datetime.min.time())
    end_dt_global = datetime.combine(semester_end, datetime.min.time())

    for event in selected_variants:
        try:
            # SINGLE EVENTS (Specific Date)
            if event['type'] == 'single':
                start_dt = datetime.strptime(f"{event['date']} {event['start_time']}", '%d.%m.%Y %H:%M')
                end_dt = datetime.strptime(f"{event['date']} {event['end_time']}", '%d.%m.%Y %H:%M')
                
                e = Event()
                e.name = event['full_label']
                e.location = event['location']
                e.begin = tz.localize(start_dt)
                e.end = tz.localize(end_dt)
                c.events.add(e)

            # RECURRING EVENTS (Every Week)
            elif event['type'] == 'recurring':
                target_weekday = weekday_map[event['weekday']]
                current = start_dt_global
                
                # Advance to first occurrence
                while current.weekday() != target_weekday:
                    current += timedelta(days=1)
                
                while current <= end_dt_global:
                    # CHECK HOLIDAY
                    current_iso_week = current.isocalendar()[1]
                    if current_iso_week in holiday_weeks:
                        # Skip this week
                        current += timedelta(days=7)
                        continue

                    start_str = f"{current.strftime('%d.%m.%Y')} {event['start_time']}"
                    end_str = f"{current.strftime('%d.%m.%Y')} {event['end_time']}"
                    
                    dt_s = datetime.strptime(start_str, '%d.%m.%Y %H:%M')
                    dt_e = datetime.strptime(end_str, '%d.%m.%Y %H:%M')
                    
                    e = Event()
                    e.name = event['full_label']
                    e.location = event['location']
                    e.begin = tz.localize(dt_s)
                    e.end = tz.localize(dt_e)
                    c.events.add(e)
                    
                    current += timedelta(days=7)

        except Exception as ex:
            print(f"Error creating event: {ex}")
            continue

    return c.serialize()

# --- MAIN APP LOGIC ---

st.title("🎓 Smart Vorlesungs-Planer")
st.markdown("Lade dein PDF hoch, gib deine Modulnummern ein, und wähle deine Gruppen. Ferien werden automatisch erkannt.")

# 1. FILE UPLOAD & SETTINGS (SIDEBAR)
with st.sidebar:
    st.header("1. Upload & Daten")
    uploaded_file = st.file_uploader("Vorlesungsverzeichnis (PDF)", type="pdf")
    
    st.header("2. Semester-Zeitraum")
    # Default to "Now" until "Now + 4 Months"
    default_start = datetime.now()
    default_end = default_start + timedelta(weeks=16)
    
    sem_start = st.date_input("Start", value=default_start)
    sem_end = st.date_input("Ende", value=default_end)
    
    st.info("ℹ️ recurring events (ohne festes Datum) werden in diesem Zeitraum erstellt.")

if not uploaded_file:
    st.info("Bitte lade links eine PDF-Datei hoch, um zu beginnen.")
    st.stop()

# 2. PARSING (Only runs once per file upload)
if 'all_events' not in st.session_state or st.session_state.get('last_file') != uploaded_file.name:
    with st.spinner("PDF wird analysiert..."):
        text = parse_pdf(uploaded_file)
        events = extract_events(text)
        st.session_state['all_events'] = events
        st.session_state['last_file'] = uploaded_file.name
        
        # Holiday Detection
        detected_holidays = detect_holiday_weeks(events, sem_start, sem_end)
        st.session_state['detected_holidays'] = detected_holidays

all_events = st.session_state['all_events']
holidays = st.session_state['detected_holidays']

# Display Holiday Info
if holidays:
    with st.expander(f"🏖️ {len(holidays)} Ferienwochen erkannt (hier klicken)"):
        st.write("In folgenden Kalenderwochen finden keine wiederkehrenden Veranstaltungen statt:")
        st.write(", ".join(map(str, holidays)))
        use_holidays = st.checkbox("Ferien berücksichtigen?", value=True)
else:
    st.caption("Keine eindeutigen Ferienwochen in der PDF erkannt.")
    use_holidays = False

st.divider()

# 3. SEARCH & FILTER
st.subheader("3. Modul-Auswahl")
user_query = st.text_input(
    "Gib deine Modulnummern ein (z.B. '1.3, 4.0')", 
    help="Du kannst '1.3' eingeben, um alle Kurse zu finden, die mit 1.3 beginnen (z.B. 1.3.1, 1.3.2)."
)

final_selection_variants = []

if user_query:
    # Split input by comma and clean up
    search_tokens = [t.strip() for t in user_query.split(',') if t.strip()]
    
    # Find matching events
    found_events = []
    for token in search_tokens:
        # Check if ID starts with token
        matches = [e for e in all_events if e['id'].startswith(token)]
        found_events.extend(matches)
    
    if not found_events:
        st.warning(f"Keine Module für '{user_query}' gefunden.")
    else:
        # Group by ID to handle logic
        # Data structure: unique_ids[id] = { 'title': '...', 'variants': [list of events] }
        grouped_modules = {}
        for e in found_events:
            eid = e['id']
            if eid not in grouped_modules:
                grouped_modules[eid] = {'title': e['title'], 'variants': []}
            grouped_modules[eid]['variants'].append(e)

        st.success(f"{len(grouped_modules)} Module gefunden. Bitte Details bestätigen:")

        # 4. RESOLVE CONFLICTS (GROUPS/SEMINARS)
        for mid, data in sorted(grouped_modules.items()):
            title = data['title']
            variants = data['variants']
            
            # Identify unique time-slots (signatures)
            # We use a dictionary keyed by signature to keep one representative event per slot
            unique_slots = {}
            for v in variants:
                if v['signature'] not in unique_slots:
                    unique_slots[v['signature']] = v
            
            slot_list = list(unique_slots.values())

            # UI Container for this Module
            with st.container():
                st.markdown(f"**{mid} {title}**")
                
                # Case A: Only 1 option -> Auto-select
                if len(slot_list) == 1:
                    st.caption(f"✅ Automatisch hinzugefügt: {slot_list[0]['weekday']} {slot_list[0]['start_time']} ({slot_list[0]['location']})")
                    final_selection_variants.append(slot_list[0])
                
                # Case B: Multiple options -> User must choose
                else:
                    st.info(f"⚠️ Dieses Modul hat {len(slot_list)} verschiedene Termine/Gruppen. Bitte wählen:")
                    
                    # Create labels for checkboxes
                    options = {f"{v['weekday']} {v['start_time']}-{v['end_time']} ({v['location']})": v for v in slot_list}
                    
                    # We use multiselect so they can pick Lecture AND Seminar if needed
                    selections = st.multiselect(
                        f"Termine für {mid} wählen:",
                        options=list(options.keys()),
                        key=f"select_{mid}"
                    )
                    
                    for sel_label in selections:
                        final_selection_variants.append(options[sel_label])
                
                st.divider()

# 5. PREVIEW & DOWNLOAD
if final_selection_variants:
    st.subheader("4. Vorschau & Download")
    
    # Sort by ID for cleaner preview
    final_selection_variants.sort(key=lambda x: x['id'])

    # Show simplified table
    preview_data = []
    for f in final_selection_variants:
        preview_data.append({
            "Modul": f"{f['id']}",
            "Titel": f"{f['title']}",
            "Wann": f"{f['weekday']} {f['start_time']}-{f['end_time']}",
            "Wo": f"{f['location']}"
        })
    st.dataframe(preview_data, hide_index=True, use_container_width=True)
    
    # Generate ICS
    active_holiday_weeks = holidays if use_holidays else []
    ics_data = generate_ics(final_selection_variants, sem_start, sem_end, active_holiday_weeks)
    
    st.download_button(
        label="📥 Kalender-Datei (.ics) herunterladen",
        data=ics_data,
        file_name="Uni_Kalender.ics",
        mime="text/calendar",
        type="primary"
    )

elif user_query:
    st.caption("Wähle oben Gruppen aus, um den Download zu aktivieren.")