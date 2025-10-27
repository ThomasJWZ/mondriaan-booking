from datetime import datetime, timedelta, date
import os
import uuid
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash

# Load .env (local development)
load_dotenv()

app = Flask(__name__)

# ✅ Secret key (keep safe in env vars on Render)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET', 'dev-secret-change-me')

# ✅ Database selection logic:
# 1. Prefer a hosted Postgres if DATABASE_URL is present (Render, etc.)
# 2. Else use DB_FILE (for SQLite on a mounted volume)
# 3. Else fall back to local 'bookings.db'
pg_url = os.environ.get("DATABASE_URL")
db_file = os.environ.get("DB_FILE")

if pg_url:
    app.config["SQLALCHEMY_DATABASE_URI"] = pg_url
elif db_file:
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_file}"
else:
    local_db_path = os.path.join(os.path.dirname(__file__), "bookings.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{local_db_path}"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Initialize SQLAlchemy
db = SQLAlchemy(app)

# Make timedelta usable in Jinja templates
app.jinja_env.globals.update(timedelta=timedelta)

# Define available rooms
ROOMS = [
    "TMS ruimte",
    "CO2 ruimte",
    "Behandelruimte",
    "Wetlab"
]


app.config['DEBUG'] = False


# Accounts config (username, display name, brand color)
ACCOUNT_DEFS = [
    {
        "username": "mondriaan_maastricht",
        "display": "Mondriaan Maastricht",
        "color": "#e53935",
        "env_pass": "PASS_MOND_MAASTRICHT",
    },
    {
        "username": "mondriaan_heerlen",
        "display": "Mondriaan Heerlen",
        "color": "#fb8c00",
        "env_pass": "PASS_MOND_HEERLEN",
    },
    {
        "username": "universiteit_maastricht",
        "display": "Universiteit Maastricht",
        "color": "#1e88e5",
        "env_pass": "PASS_UM",
    },
    {
        "username": "mumc",
        "display": "MUMC+",
        "color": "#8e24aa",
        "env_pass": "PASS_MUMC",
    },
]

db = SQLAlchemy(app)

# ---------- Models ----------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)      # e.g., "mondriaan-maastricht"
    display_name = db.Column(db.String(128), nullable=False)              # e.g., "Mondriaan Maastricht"
    color = db.Column(db.String(16), nullable=False)                      # hex color, e.g., "#e53935"
    password_hash = db.Column(db.String(255), nullable=False)

    def set_password(self, plaintext: str):
        self.password_hash = generate_password_hash(plaintext)

    def check_password(self, plaintext: str) -> bool:
        return check_password_hash(self.password_hash, plaintext)

class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room = db.Column(db.String(64), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    account = db.Column(db.String(64), nullable=False)   # username of the account
    who = db.Column(db.String(120))  # 👈 NEW: person name (optional)
    start = db.Column(db.DateTime, nullable=False)
    end = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    series_id = db.Column(db.String(36))

    def as_dict(self):
        return {
            'id': self.id,
            'room': self.room,
            'title': self.title,
            'account': self.account,
            'who': self.who,  # 👈 include in API too
            'start': self.start.isoformat(),
            'end': self.end.isoformat(),
            'series_id': self.series_id,
        }

# ---------- Helpers ----------

def week_bounds(start_date: date):
    monday = start_date - timedelta(days=start_date.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday

def parse_local_datetime(field):
    s = request.form.get(field, '').strip()
    try:
        return datetime.strptime(s, '%Y-%m-%dT%H:%M')
    except ValueError:
        return None

def get_series_occurrences(series_id: str):
    return Booking.query.filter_by(series_id=series_id).order_by(Booking.start.asc()).all()

def infer_series_pattern(occ_list):
    """Infer (start_anchor_date, weekdays_set, end_date) from existing occurrences."""
    if not occ_list:
        return None, set(), None
    dates = [o.start.date() for o in occ_list]
    start_anchor = min(dates)                          # anchor for regenerating pattern
    end_date = max(dates)                              # current series end
    weekdays = {d.weekday() for d in dates}            # e.g. {0,2,4}
    return start_anchor, weekdays, end_date


def current_user():
    """Return logged-in User object or None."""
    uname = session.get('user')
    if not uname:
        return None
    return User.query.filter_by(username=uname).first()

def account_colors_map():
    """Return {username: hexcolor} for quick lookup in templates."""
    return {u.username: u.color for u in User.query.all()}

def account_display_map():
    return {u.username: u.display_name for u in User.query.all()}

def seed_users_from_env():
    """Create the 4 accounts if they don't exist yet; read passwords from .env."""
    for spec in ACCOUNT_DEFS:
        uname = spec["username"]
        disp = spec["display"]
        col = spec["color"]
        env_key = spec["env_pass"]
        pw = os.getenv(env_key)
        # If no password is provided in .env, skip seeding for that account
        if not pw:
            continue
        u = User.query.filter_by(username=uname).first()
        if not u:
            u = User(username=uname, display_name=disp, color=col, password_hash="")
            u.set_password(pw)
            db.session.add(u)
    db.session.commit()

# ---------- Routes ----------

@app.route('/login', methods=['GET', 'POST'])
def login():
    # Ensure users exist (first run)
    if User.query.count() == 0:
        seed_users_from_env()

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session['authed'] = True
            session['user'] = user.username
            flash(f'Ingelogd als {user.display_name}.', 'success')
            return redirect(url_for('index'))
        flash('Onjuiste gebruikersnaam of wachtwoord.', 'error')
    return render_template('login.html', authed=session.get('authed', False), me=current_user())

@app.route('/logout')
def logout():
    session.pop('authed', None)
    session.pop('user', None)
    flash('Uitgelogd.', 'info')
    return redirect(url_for('index'))

@app.route('/')
def index():
    # Ensure users exist (first run)
    if User.query.count() == 0:
        seed_users_from_env()

    qs = request.args.get('week_start')
    jump_date = request.args.get('jump_date')  # 👈 NEW: from date picker
    today = date.today()

    if jump_date:
        # 👇 User selected a specific date — find Monday of that week
        try:
            target = datetime.strptime(jump_date, "%Y-%m-%d").date()
            start_d = target - timedelta(days=target.weekday())
        except ValueError:
            start_d = today
    elif qs:
        # 👇 Regular week navigation logic
        try:
            start_d = datetime.strptime(qs, '%Y-%m-%d').date()
        except ValueError:
            start_d = today
    else:
        start_d = today

    week_start, week_end = week_bounds(start_d)
    start_dt = datetime.combine(week_start, datetime.min.time())
    end_dt = datetime.combine(week_end, datetime.max.time())

    bookings = Booking.query.filter(
        Booking.start <= end_dt,
        Booking.end >= start_dt
    ).order_by(Booking.start.asc()).all()

    # organize by room -> day
    grid = {room: {(week_start + timedelta(days=i)): [] for i in range(7)} for room in ROOMS}
    for b in bookings:
        day_key = b.start.date()
        if b.room in grid and week_start <= day_key <= week_end:
            grid[b.room][day_key].append(b)

    prev_week = (week_start - timedelta(days=7)).strftime('%Y-%m-%d')
    next_week = (week_start + timedelta(days=7)).strftime('%Y-%m-%d')

    return render_template(
        'index.html',
        rooms=ROOMS,
        week_start=week_start,
        week_end=week_end,
        grid=grid,
        prev_week=prev_week,
        next_week=next_week,
        authed=session.get('authed', False),
        me=current_user(),
        acct_colors=account_colors_map(),
        acct_display=account_display_map()
    )

@app.route('/book/new', methods=['GET', 'POST'])
def new_booking():
    if not session.get('authed'):
        flash('Login vereist om te boeken.', 'error')
        return redirect(url_for('login'))

    me = current_user()
    if not me:
        session.clear()
        flash('Sessie verlopen. Log opnieuw in.', 'error')
        return redirect(url_for('login'))

    if request.method == 'POST':
        room = request.form.get('room')
        title = request.form.get('title', '').strip()
        who = request.form.get('who', '').strip()
        date_str = request.form.get('date')
        start_time = request.form.get('start_time')
        end_time = request.form.get('end_time')
        repeat_days = request.form.getlist('repeat_days')
        repeat_end = request.form.get('repeat_end')

        # basic validation
        if room not in ROOMS:
            flash('Ongeldige ruimte.', 'error')
            return render_template('new_booking.html', rooms=ROOMS, me=me, hide_login_on_this_page=True)
        if not all([title, date_str, start_time, end_time]):
            flash('Titel, datum, start en eindtijd zijn verplicht.', 'error')
            return render_template('new_booking.html', rooms=ROOMS, me=me, hide_login_on_this_page=True)

        try:
            base_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            start_dt = datetime.combine(base_date, datetime.strptime(start_time, '%H:%M').time())
            end_dt = datetime.combine(base_date, datetime.strptime(end_time, '%H:%M').time())
        except ValueError:
            flash('Ongeldige datum of tijd.', 'error')
            return render_template('new_booking.html', rooms=ROOMS, me=me, hide_login_on_this_page=True)

        if end_dt <= start_dt:
            flash('Eindtijd moet na starttijd liggen.', 'error')
            return render_template('new_booking.html', rooms=ROOMS, me=me, hide_login_on_this_page=True)

        # Build a list of dates to book (including recurrences)
        booking_dates = [base_date]

        if repeat_days and repeat_end:
            try:
                repeat_end_date = datetime.strptime(repeat_end, '%Y-%m-%d').date()
                repeat_weekdays = set(int(d) for d in repeat_days)
                cur = base_date + timedelta(days=1)
                while cur <= repeat_end_date:
                    if cur.weekday() in repeat_weekdays:
                        booking_dates.append(cur)
                    cur += timedelta(days=1)
            except ValueError:
                flash('Ongeldige einddatum voor herhaling.', 'error')
                return render_template('new_booking.html', rooms=ROOMS, me=me, hide_login_on_this_page=True)

        created = 0
        series_id = None
        if len(booking_dates) > 1:
            series_id = str(uuid.uuid4())  # 👈 give this set a shared id
        for d in booking_dates:
            sdt = datetime.combine(d, start_dt.time())
            edt = datetime.combine(d, end_dt.time())

            overlap = Booking.query.filter(
                Booking.room == room,
                Booking.start < edt,
                Booking.end > sdt
            ).first()
            if overlap:
                flash(f'Conflict: reeds een boeking op {d.strftime("%d-%m-%Y")}.', 'error')
                continue

            b = Booking(room=room, title=title, account=me.username, who=who or None, start=sdt, end=edt, series_id=series_id)
            db.session.add(b)
            created += 1

        db.session.commit()
        flash(f'{created} boeking(en) aangemaakt.', 'success')
        return redirect(url_for('index'))

    # default form values
    default_start = datetime.now().replace(minute=0, second=0, microsecond=0)
    default_end = default_start + timedelta(hours=1)
    return render_template('new_booking.html',
                       rooms=ROOMS,
                       default_start=default_start,
                       default_end=default_end,
                       me=me,
                       hide_login_on_this_page=True)

@app.route('/book/edit/<int:booking_id>', methods=['GET', 'POST'])
def edit_booking(booking_id):
    if not session.get('authed'):
        flash('Login vereist om te bewerken.', 'error')
        return redirect(url_for('login'))

    me = current_user()
    if not me:
        session.clear()
        flash('Sessie verlopen. Log opnieuw in.', 'error')
        return redirect(url_for('login'))

    b = Booking.query.get_or_404(booking_id)

    if request.method == 'POST':
        room = request.form.get('room')
        title = request.form.get('title', '').strip()
        who = request.form.get('who', '').strip()
        date_str = request.form.get('date')
        start_time = request.form.get('start_time')
        end_time = request.form.get('end_time')

        if room not in ROOMS or not all([title, date_str, start_time, end_time]):
            flash('Titel, datum, start en eindtijd zijn verplicht.', 'error')
            # Re-render with any series info we can infer
            start_anchor, wd_set, series_end = (None, set(), None)
            if b.series_id:
                occs = get_series_occurrences(b.series_id)
                start_anchor, wd_set, series_end = infer_series_pattern(occs)
            return render_template('edit_booking.html',
                                   rooms=ROOMS, booking=b,
                                   form_date=date_str or b.start.strftime('%Y-%m-%d'),
                                   form_start=start_time or b.start.strftime('%H:%M'),
                                   form_end=end_time or b.end.strftime('%H:%M'),
                                   series_weekdays=wd_set,
                                   series_end=series_end.strftime('%Y-%m-%d') if series_end else '',
                                   has_series=bool(b.series_id),
                                   hide_login_on_this_page=True)

        # parse new datetime values for the *clicked* occurrence
        try:
            new_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            new_start = datetime.combine(new_date, datetime.strptime(start_time, '%H:%M').time())
            new_end = datetime.combine(new_date, datetime.strptime(end_time, '%H:%M').time())
        except ValueError:
            flash('Ongeldige datum of tijd.', 'error')
            start_anchor, wd_set, series_end = (None, set(), None)
            if b.series_id:
                occs = get_series_occurrences(b.series_id)
                start_anchor, wd_set, series_end = infer_series_pattern(occs)
            return render_template('edit_booking.html',
                                   rooms=ROOMS, booking=b,
                                   form_date=date_str, form_start=start_time, form_end=end_time,
                                   series_weekdays=wd_set,
                                   series_end=series_end.strftime('%Y-%m-%d') if series_end else '',
                                   has_series=bool(b.series_id),
                                   hide_login_on_this_page=True)

        if new_end <= new_start:
            flash('Eindtijd moet na starttijd liggen.', 'error')
            start_anchor, wd_set, series_end = (None, set(), None)
            if b.series_id:
                occs = get_series_occurrences(b.series_id)
                start_anchor, wd_set, series_end = infer_series_pattern(occs)
            return render_template('edit_booking.html',
                                   rooms=ROOMS, booking=b,
                                   form_date=date_str, form_start=start_time, form_end=end_time,
                                   series_weekdays=wd_set,
                                   series_end=series_end.strftime('%Y-%m-%d') if series_end else '',
                                   has_series=bool(b.series_id),
                                   hide_login_on_this_page=True)

        if b.series_id:
            # ----- SERIES EDIT -----
            # Read recurrence inputs
            repeat_days = request.form.getlist('repeat_days')               # list of strings '0'..'6'
            repeat_end = request.form.get('repeat_end')                     # 'YYYY-MM-DD' or ''
            occs = get_series_occurrences(b.series_id)
            anchor_date, current_days, current_end = infer_series_pattern(occs)

            # If user provided nothing, fall back to current pattern
            if not repeat_days:
                repeat_weekdays = current_days
            else:
                try:
                    repeat_weekdays = {int(d) for d in repeat_days}
                except ValueError:
                    flash('Ongeldige weekdagen voor herhaling.', 'error')
                    return render_template('edit_booking.html',
                                           rooms=ROOMS, booking=b,
                                           form_date=date_str, form_start=start_time, form_end=end_time,
                                           series_weekdays=current_days,
                                           series_end=current_end.strftime('%Y-%m-%d') if current_end else '',
                                           has_series=True, hide_login_on_this_page=True)
            if not repeat_weekdays:
                flash('Kies minimaal één herhalingsdag.', 'error')
                return render_template('edit_booking.html',
                                       rooms=ROOMS, booking=b,
                                       form_date=date_str, form_start=start_time, form_end=end_time,
                                       series_weekdays=current_days,
                                       series_end=current_end.strftime('%Y-%m-%d') if current_end else '',
                                       has_series=True, hide_login_on_this_page=True)

            try:
                new_series_end = datetime.strptime(repeat_end, '%Y-%m-%d').date() if repeat_end else current_end
            except ValueError:
                flash('Ongeldige einddatum voor herhaling.', 'error')
                return render_template('edit_booking.html',
                                       rooms=ROOMS, booking=b,
                                       form_date=date_str, form_start=start_time, form_end=end_time,
                                       series_weekdays=current_days,
                                       series_end=current_end.strftime('%Y-%m-%d') if current_end else '',
                                       has_series=True, hide_login_on_this_page=True)

            if not new_series_end or new_series_end < anchor_date:
                flash('Einddatum ligt vóór de start van de reeks.', 'error')
                return render_template('edit_booking.html',
                                       rooms=ROOMS, booking=b,
                                       form_date=date_str, form_start=start_time, form_end=end_time,
                                       series_weekdays=current_days,
                                       series_end=current_end.strftime('%Y-%m-%d') if current_end else '',
                                       has_series=True, hide_login_on_this_page=True)

            # Build new dates (inclusive)
            new_dates = []
            cur = anchor_date
            while cur <= new_series_end:
                if cur.weekday() in repeat_weekdays:
                    new_dates.append(cur)
                cur += timedelta(days=1)

            # Pre-check conflicts vs other bookings (exclude this series)
            conflicts = []
            for d in new_dates:
                sdt = datetime.combine(d, new_start.time())
                edt = datetime.combine(d, new_end.time())
                conflict = Booking.query.filter(
                    Booking.room == room,
                    Booking.series_id != b.series_id,
                    Booking.start < edt,
                    Booking.end > sdt
                ).first()
                if conflict:
                    conflicts.append(d.strftime('%d-%m-%Y'))
            if conflicts:
                flash('Conflicten op: ' + ', '.join(conflicts) + '. Reeks niet gewijzigd.', 'error')
                return render_template('edit_booking.html',
                                       rooms=ROOMS, booking=b,
                                       form_date=date_str, form_start=start_time, form_end=end_time,
                                       series_weekdays=repeat_weekdays,
                                       series_end=new_series_end.strftime('%Y-%m-%d'),
                                       has_series=True, hide_login_on_this_page=True)

            # No conflicts: replace entire series with new pattern
            # Keep same series_id and account
            series_id = b.series_id
            account = b.account

            # Delete old series
            for occ in occs:
                db.session.delete(occ)
            db.session.flush()

            # Create new series
            for d in new_dates:
                sdt = datetime.combine(d, new_start.time())
                edt = datetime.combine(d, new_end.time())
                nb = Booking(room=room, title=title, who=who or None,
                             account=account, start=sdt, end=edt, series_id=series_id)
                db.session.add(nb)

            db.session.commit()
            flash(f'Reeks bijgewerkt ({len(new_dates)} boekingen).', 'success')
            return redirect(url_for('index'))

        else:
            # ----- SINGLE EDIT -----
            conflict = Booking.query.filter(
                Booking.id != b.id,
                Booking.room == room,
                Booking.start < new_end,
                Booking.end > new_start
            ).first()
            if conflict:
                flash('Conflict: er bestaat al een boeking in deze periode.', 'error')
                return render_template('edit_booking.html',
                                       rooms=ROOMS, booking=b,
                                       form_date=date_str, form_start=start_time, form_end=end_time,
                                       has_series=False, hide_login_on_this_page=True)

            b.room = room
            b.title = title
            b.who = who or None
            b.start = new_start
            b.end = new_end
            db.session.commit()
            flash('Boeking bijgewerkt.', 'success')
            return redirect(url_for('index'))

    # GET — prefill
    form_date = b.start.strftime('%Y-%m-%d')
    form_start = b.start.strftime('%H:%M')
    form_end = b.end.strftime('%H:%M')

    # If series, infer pattern to prefill the checkboxes and end date
    series_weekdays, series_end = set(), ''
    if b.series_id:
        occs = get_series_occurrences(b.series_id)
        _, wd_set, end_d = infer_series_pattern(occs)
        series_weekdays = wd_set
        series_end = end_d.strftime('%Y-%m-%d') if end_d else ''

    return render_template(
        'edit_booking.html',
        rooms=ROOMS,
        booking=b,
        form_date=form_date,
        form_start=form_start,
        form_end=form_end,
        series_weekdays=series_weekdays,
        series_end=series_end,
        has_series=bool(b.series_id),
        hide_login_on_this_page=True
    )


@app.route('/book/series/<series_id>/delete', methods=['POST'])
def delete_series(series_id):
    if not session.get('authed'):
        flash('Login vereist.', 'error')
        return redirect(url_for('login'))
    me = current_user()
    if not me:
        session.clear()
        flash('Sessie verlopen. Log opnieuw in.', 'error')
        return redirect(url_for('login'))

    occs = Booking.query.filter_by(series_id=series_id).all()
    count = len(occs)
    for o in occs:
        db.session.delete(o)
    db.session.commit()
    flash(f'Reeks verwijderd ({count} boekingen).', 'info')
    return redirect(url_for('index'))


@app.route('/book/delete/<int:booking_id>', methods=['POST'])
def delete_booking(booking_id):
    if not session.get('authed'):
        flash('Login vereist om te verwijderen.', 'error')
        return redirect(url_for('login'))
    b = Booking.query.get_or_404(booking_id)
    db.session.delete(b)
    db.session.commit()
    flash('Boeking verwijderd.', 'info')
    return redirect(url_for('index'))

# simple JSON feed (optional)
@app.route('/api/bookings')
def api_bookings():
    start = request.args.get('start')
    end = request.args.get('end')
    q = Booking.query
    if start:
        try:
            sdt = datetime.fromisoformat(start)
            q = q.filter(Booking.end >= sdt)
        except ValueError:
            pass
    if end:
        try:
            edt = datetime.fromisoformat(end)
            q = q.filter(Booking.start <= edt)
        except ValueError:
            pass
    items = q.order_by(Booking.start.asc()).all()
    return jsonify([b.as_dict() for b in items])

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        # seed on first run if needed
        if User.query.count() == 0:
            seed_users_from_env()
    app.run(debug=True)
