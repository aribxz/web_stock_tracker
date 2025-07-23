import plotly.graph_objs as go
from markupsafe import Markup
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
import yfinance as yf
from datetime import datetime
from flask_login import (
    UserMixin, LoginManager, login_user,
    logout_user, login_required, current_user
)

from flask_bcrypt import Bcrypt
from flask_dance.contrib.google import make_google_blueprint, google

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///stocks.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = "your_super_secret_key_here"  # Set a secure key

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

# OAuth Setup
google_bp = make_google_blueprint(
    client_id="YOUR_CLIENT_ID",
    client_secret="YOUR_SECRET",
    redirect_to="google_login"
)
app.register_blueprint(google_bp, url_prefix="/login")

# User Loader
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Models
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(256))
    email = db.Column(db.String(150), unique=True)
    stocks = db.relationship('Stock', backref='owner', lazy=True)

class Stock(db.Model):
    sno = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(200), nullable=False)
    date_created = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

# Routes
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form['username']
        email = request.form['email']
        password = bcrypt.generate_password_hash(request.form['password']).decode('utf-8')
        user = User(username=username, email=email, password=password)
        db.session.add(user)
        db.session.commit()
        return redirect("/login")
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        user = User.query.filter_by(username=username).first()
        if user and bcrypt.check_password_hash(user.password, password):
            login_user(user)
            return redirect("/")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/login")

@app.route("/google")
def google_login():
    if not google.authorized:
        return redirect(url_for("google.login"))
    resp = google.get("/oauth2/v2/userinfo")
    info = resp.json()
    user = User.query.filter_by(email=info["email"]).first()
    if not user:
        user = User(username=info["name"], email=info["email"])
        db.session.add(user)
        db.session.commit()
    login_user(user)
    return redirect("/")

@app.route("/", methods=["GET", "POST"])
def home():
    if current_user.is_authenticated:
        if request.method == "POST":
            symbol = request.form['title'].upper()
            if not Stock.query.filter_by(symbol=symbol, user_id=current_user.id).first():
                new_stock = Stock(symbol=symbol, user_id=current_user.id)
                db.session.add(new_stock)
                db.session.commit()

        all_stocks = get_all_stock_data(current_user.id)
        return render_template("index.html", allStocks=all_stocks, user=current_user)
    else:
        # Show public example stocks
        public_stocks = []
        for symbol in ["AAPL", "TSLA", "AMZN"]:
            try:
                public_stocks.append(fetch_stock_info(symbol))
            except Exception as e:
                print(f"⚠️ Error fetching {symbol}: {e}")
        return render_template("index.html", allStocks=public_stocks, user=None)

@app.route("/delete/<int:sno>")
@login_required
def delete(sno):
    stock_entry = Stock.query.filter_by(sno=sno, user_id=current_user.id).first()
    if stock_entry:
        db.session.delete(stock_entry)
        db.session.commit()
    return redirect("/")

@app.route("/chart/<symbol>")
@login_required
def chart(symbol):
    stock = yf.Ticker(symbol)
    hist = stock.history(period="7d")
    labels = hist.index.strftime('%Y-%m-%d').tolist()
    prices = hist["Close"].round(2).tolist()

    info = stock.info
    extra_data = {
        "prev_close": info.get("previousClose", "-"),
        "current_price": info.get("regularMarketPrice", "-"),
        "day_high": info.get("dayHigh", "-"),
        "day_low": info.get("dayLow", "-"),
        "volume": info.get("volume", "-"),
        "market_cap": info.get("marketCap", "-"),
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    return render_template("chart.html", symbol=symbol.upper(), labels=labels, prices=prices, extra_data=extra_data)

# Utilities
def get_all_stock_data(user_id):
    stocks = Stock.query.filter_by(user_id=user_id).all()
    data = []
    for stock in stocks:
        try:
            stock_data = fetch_stock_info(stock.symbol)
            stock_data["sno"] = stock.sno
            data.append(stock_data)
        except Exception as e:
            print(f"⚠️ Error fetching {stock.symbol}: {e}")
    return data

@app.route("/compare", methods=["GET", "POST"])
@login_required
def compare():
    stock_list = [stock.symbol for stock in Stock.query.filter_by(user_id=current_user.id)]
    selected1 = selected2 = stock1_data = stock2_data = None
    stock1_history = stock2_history = None

    if request.method == "POST":
        selected1 = request.form.get("stock1")
        selected2 = request.form.get("stock2")

        if selected1 and selected2:
            stock1_data = fetch_stock_info(selected1)
            stock2_data = fetch_stock_info(selected2)

            stock1_history = get_history(selected1)
            stock2_history = get_history(selected2)

    return render_template("compare.html",
        stock_list=stock_list,
        selected1=selected1,
        selected2=selected2,
        stock1_data=stock1_data,
        stock2_data=stock2_data,
        stock1_history=stock1_history,
        stock2_history=stock2_history
    )

def get_history(symbol):
    stock = yf.Ticker(symbol)
    hist = stock.history(period="7d")
    labels = hist.index.strftime('%Y-%m-%d').tolist()
    prices = hist["Close"].round(2).tolist()
    return {"labels": labels, "prices": prices}



def get_stock_comparison_data(symbol):
    stock = yf.Ticker(symbol)
    hist = stock.history(period="7d")
    dates = hist.index.strftime('%Y-%m-%d').tolist()
    prices = hist["Close"].round(2).tolist()

    info = stock.info
    prev_close = info.get("previousClose", 0)
    current_price = info.get("regularMarketPrice", 0)
    high = info.get("dayHigh", 0)
    low = info.get("dayLow", 0)
    change_percent = ((current_price - prev_close) / prev_close * 100) if prev_close else 0

    return {
        "symbol": symbol.upper(),
        "dates": dates,
        "prices": prices,
        "current_price": current_price,
        "prev_close": prev_close,
        "high": high,
        "low": low,
        "change_percent": round(change_percent, 2),
        "volume": info.get("volume", "-"),
        "market_cap": info.get("marketCap", "-")
    }


def fetch_stock_info(symbol):
    stock = yf.Ticker(symbol)
    info = stock.info
    prev_close = info.get("previousClose", 0)
    current_price = info.get("regularMarketPrice", 0)
    day_high = info.get("dayHigh", 0)
    day_low = info.get("dayLow", 0)

    change_percent = ((current_price - prev_close) / prev_close * 100) if prev_close else 0

    return {
        "Symbol": symbol.upper(),
        "Price": round(current_price, 2),
        "Change %": round(change_percent, 2),
        "High": round(day_high, 2) if day_high else "-",
        "Low": round(day_low, 2) if day_low else "-",
        "Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

if __name__ == "__main__":
    app.run(debug=True)
