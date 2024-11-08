from datetime import datetime
from flask import Flask, render_template, request, redirect
import sqlite3

app = Flask(__name__)

MAX_POINTS = 21
RATING_FACTOR = 21

def init_db():
    with sqlite3.connect('results.db') as conn:
        cursor = conn.cursor()

        # Create table for players
        cursor.execute('''CREATE TABLE IF NOT EXISTS players (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            name TEXT NOT NULL,
                            wins INTEGER DEFAULT 0,
                            losses INTEGER DEFAULT 0,
                            rating INTEGER DEFAULT 1200,
                            points_won INTEGER DEFAULT 0,
                            points_lost INTEGER DEFAULT 0)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS teams (
                    team_id TEXT PRIMARY KEY,
                    player1_id INTEGER,
                    player2_id INTEGER,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    FOREIGN KEY (player1_id) REFERENCES players (id),
                    FOREIGN KEY (player2_id) REFERENCES players (id)
                )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS matches (
                            match_id INTEGER PRIMARY KEY AUTOINCREMENT,
                            team1_id TEXT,
                            team2_id TEXT,
                            date TEXT,
                            team1_points INTEGER,
                            team2_points INTEGER,
                            winner INTEGER,
                            FOREIGN KEY (team1_id) REFERENCES teams (team_id),
                            FOREIGN KEY (team2_id) REFERENCES teams (team_id))''')
        
        conn.commit()

@app.template_filter('team_name')
def team_name(team_id):
    conn = sqlite3.connect('results.db')
    cursor = conn.cursor()
    cursor.execute("SELECT p1.name, p2.name FROM teams t JOIN players p1 ON t.player1_id = p1.id JOIN players p2 ON t.player2_id = p2.id WHERE t.team_id = ?", (team_id,))
    team = cursor.fetchone()
    conn.close()
    return f"{team[0]}, {team[1]}"


# Home route (displays matches and form)
@app.route('/')
def index():
    conn = sqlite3.connect('results.db')
    cursor = conn.cursor()

    # Fetch the matches, ordered by date (most recent first)
    cursor.execute('''
        SELECT m.match_id, m.date, t1.team_id AS team1_id, t2.team_id AS team2_id, 
               m.team1_points, m.team2_points
        FROM matches m
        JOIN teams t1 ON m.team1_id = t1.team_id
        JOIN teams t2 ON m.team2_id = t2.team_id
        ORDER BY m.date DESC
    ''')

    matches = cursor.fetchall()
    conn.close()

    # Group matches by date
    grouped_matches = {}
    for match in matches:
        match_date = datetime.strptime(match[1], "%Y-%m-%d").strftime("%A, %dth %b %Y")  # Format: "Thursday, 12th Jan 2024"
        
        if match_date not in grouped_matches:
            grouped_matches[match_date] = []
        
        grouped_matches[match_date].append(match)
    
    return render_template('index.html', grouped_matches=grouped_matches)

@app.route('/register', methods=['GET', 'POST'])
def register_player():
    if request.method == 'POST':
        name = request.form['name']
        
        with sqlite3.connect('results.db') as conn:
            cursor = conn.cursor()
            
            # Insert the new player into the players table
            cursor.execute('''INSERT INTO players (name) VALUES (?)''', (name,))
            conn.commit()

            # Get the ID of the newly registered player
            new_player_id = cursor.lastrowid

            # Fetch all existing players from the database
            cursor.execute('''SELECT id FROM players WHERE id != ?''', (new_player_id,))
            existing_players = cursor.fetchall()

            # Create new teams with the new player as player 2
            for player in existing_players:
                existing_player_id = player[0]
                
                # Ensure the player with the smaller ID is always player 1
                if new_player_id > existing_player_id:
                    player1_id, player2_id = existing_player_id, new_player_id
                else:
                    player1_id, player2_id = new_player_id, existing_player_id

                # Insert the new team into the teams table
                team_id = f"{player1_id}&{player2_id}"  # Concatenate the player IDs to form team ID
                cursor.execute('''
                    INSERT INTO teams (team_id, player1_id, player2_id) 
                    VALUES (?, ?, ?)
                ''', (team_id, player1_id, player2_id))
            
            conn.commit()

        return redirect('/players')  # Redirect to players list (or another appropriate page)

    return render_template('register.html')


# Route to view the list of players and their records
@app.route('/players')
def player_list():
    conn = sqlite3.connect('results.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM players ORDER BY rating DESC')
    players = cursor.fetchall()
    conn.close()
    return render_template('player_list.html', players=players)

def create_team(player1_id, player2_id):
    # Ensure the player with the smaller ID is first
    if player1_id > player2_id:
        player1_id, player2_id = player2_id, player1_id

    # Generate team_id as "p1name&p2name"
    conn = sqlite3.connect('results.db')
    cursor = conn.cursor()
    
    # Retrieve player names for team_id creation
    cursor.execute("SELECT name FROM players WHERE id = ?", (player1_id,))
    player1_name = cursor.fetchone()[0]
    cursor.execute("SELECT name FROM players WHERE id = ?", (player2_id,))
    player2_name = cursor.fetchone()[0]
    team_id = f"{player1_name}&{player2_name}"

    # Check if the team already exists
    cursor.execute("SELECT * FROM teams WHERE team_id = ?", (team_id,))
    existing_team = cursor.fetchone()
    
    # If the team doesn't exist, add it
    if not existing_team:
        cursor.execute("INSERT INTO teams (team_id, player1_id, player2_id) VALUES (?, ?, ?)",
                       (team_id, player1_id, player2_id))
        conn.commit()
    
    conn.close()
    return team_id


def get_players():
    with sqlite3.connect('results.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM players")
        return cursor.fetchall()

@app.route('/enter_result')
def enter_result():
    players = get_players()
    print("Players list:", players)  # Check the console output here
    return render_template('enter_result.html', players=players)

def update_player_ratings(winner, team1_points, team2_points, team1_players, team2_players, cursor):
    """
    Update player ratings (points_won and points_lost) based on the match result, 
    with additional ELO rating adjustment based on team ratings and points difference.
    
    Args:
        winner (int): The winning team (1 for team1, 2 for team2).
        team1_points (int): Total points scored by team 1.
        team2_points (int): Total points scored by team 2.
        team1_players (list): List of player IDs for team 1.
        team2_players (list): List of player IDs for team 2.
        cursor (sqlite3.Cursor): SQLite cursor to execute database queries.
    """
    # Fetch player ratings
    cursor.execute("SELECT rating FROM players WHERE id = ?", (team1_players[0],))
    rating1 = cursor.fetchone()[0]
    cursor.execute("SELECT rating FROM players WHERE id = ?", (team1_players[1],))
    rating2 = cursor.fetchone()[0]
    cursor.execute("SELECT rating FROM players WHERE id = ?", (team2_players[0],))
    rating3 = cursor.fetchone()[0]
    cursor.execute("SELECT rating FROM players WHERE id = ?", (team2_players[1],))
    rating4 = cursor.fetchone()[0]

    # Calculate the average team ratings
    team1_avg_rating = (rating1 + rating2) / 2
    team2_avg_rating = (rating3 + rating4) / 2

    # Calculate expected scores using the ELO formula
    def expected_score(R_a, R_b):
        return 1 / (1 + 10 ** ((R_b - R_a) / 400))
    
    E_team1 = expected_score(team1_avg_rating, team2_avg_rating)
    E_team2 = expected_score(team2_avg_rating, team1_avg_rating)
    
    # Determine actual outcome
    if winner == 1:
        S_team1, S_team2 = 1, 0  # Team 1 wins
    else:
        S_team1, S_team2 = 0, 1  # Team 2 wins

    # K-factor (determines how much rating can change)
    K = RATING_FACTOR

    # Calculate the rating change for each team
    rating_change_team1 = K * (S_team1 - E_team1) * (1 + abs(team1_points - team2_points) / MAX_POINTS)
    rating_change_team2 = K * (S_team2 - E_team2) * (1 + abs(team1_points - team2_points) / MAX_POINTS)

    # Update points and ELO ratings for players in the winning team
    for player in team1_players if winner == 1 else team2_players:
        cursor.execute("UPDATE players SET points_won = points_won + ?, points_lost = points_lost + ?, wins = wins + 1 WHERE id = ?", 
                       (team1_points if winner == 1 else team2_points, team2_points if winner == 1 else team1_points, player))
        # Update ELO rating
        cursor.execute("UPDATE players SET rating = rating + ? WHERE id = ?", 
                       (rating_change_team1 if winner == 1 else rating_change_team2, player))

    # Update points and ELO ratings for players in the losing team
    for player in team2_players if winner == 1 else team1_players:
        cursor.execute("UPDATE players SET points_won = points_won + ?, points_lost = points_lost + ?, losses = losses + 1 WHERE id = ?", 
                       (team2_points if winner == 1 else team1_points, team1_points if winner == 1 else team2_points, player))   
        # Update ELO rating
        cursor.execute("UPDATE players SET rating = rating + ? WHERE id = ?", 
                       (rating_change_team2 if winner == 1 else rating_change_team1, player))

    # Commit the changes to the database
    cursor.connection.commit()

def update_team_scores(winning_team_id, losing_team_id, cursor):
    """
    Update the scores (wins, losses, and points) for both the winning and losing teams.

    Args:
        winning_team_id (int): The ID of the winning team.
        losing_team_id (int): The ID of the losing team.
        cursor (sqlite3.Cursor): SQLite cursor to execute database queries.
    """
    # Update the winning team (add a win and points)
    cursor.execute("UPDATE teams SET wins = wins + 1 WHERE team_id = ?", (winning_team_id,))
    
    # Update the losing team (add a loss)
    cursor.execute("UPDATE teams SET losses = losses + 1 WHERE team_id = ?", (losing_team_id,))

    # Commit the changes to the database
    cursor.connection.commit()


@app.route('/submit_result', methods=['POST'])
def submit_result():
    team1_player1 = int(request.form['team1_player1'])
    team1_player2 = int(request.form['team1_player2'])
    team2_player1 = int(request.form['team2_player1'])
    team2_player2 = int(request.form['team2_player2'])
    team1_points = int(request.form['team1_points'])
    team2_points = int(request.form['team2_points'])

     # Check if any player appears twice
    if len(set([team1_player1, team1_player2, team2_player1, team2_player2])) != 4:
        return "Error: A player cannot appear twice in the same match."

    # Arrange player IDs to enforce ordering
    if team1_player1 > team1_player2:
        team1_player1, team1_player2 = team1_player2, team1_player1
    if team2_player1 > team2_player2:
        team2_player1, team2_player2 = team2_player2, team2_player1

      # Automatically determine winner based on points
    if team1_points > team2_points:
        winner = 1
    elif team2_points > team1_points:
        winner = 2
    else:
        winner = 0  # If there's a tie, set winner to 0 or handle tie logic

    # Get or create team IDs
    team1_id = create_team(team1_player1, team1_player2)
    team2_id = create_team(team2_player1, team2_player2)

    if winner == 1:
        [winning_team_id, losing_team_id] = [team1_id, team2_id]
    else:
        [winning_team_id, losing_team_id] = [team2_id, team1_id]

    team1_players = [team1_player1, team1_player2]
    team2_players = [team2_player1, team2_player2]

    # Insert the match
    match_date = datetime.now().strftime("%Y-%m-%d")
    with sqlite3.connect('results.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''INSERT INTO matches (team1_id, team2_id, date, team1_points, team2_points, winner)
                          VALUES (?, ?, ?, ?, ?, ?)''', (team1_id, team2_id, match_date, team1_points, team2_points, winner))
        update_team_scores(winning_team_id, losing_team_id, cursor)
        update_player_ratings(winner, team1_points, team2_points, team1_players, team2_players, cursor)
        conn.commit()
    
    return redirect('/enter_result')

@app.route('/view_teams')
def view_teams():
    with sqlite3.connect('results.db') as conn:
        cursor = conn.cursor()

        # Query to fetch teams with player names and ratings
        query = '''
        SELECT t.team_id, 
               p1.name AS player1_name, p2.name AS player2_name, 
               t.wins, t.losses, 
               p1.rating AS player1_rating, p2.rating AS player2_rating
        FROM teams t
        JOIN players p1 ON t.player1_id = p1.id
        JOIN players p2 ON t.player2_id = p2.id
        ORDER BY t.wins DESC
        '''
        cursor.execute(query)
        teams = cursor.fetchall()

        # Calculate average rating for each team
        teams_info = []
        for team in teams:
            team_id, player1_name, player2_name, wins, losses, player1_rating, player2_rating = team
            avg_rating = (player1_rating + player2_rating) / 2
            teams_info.append({
                'team_id': team_id,
                'players': f'{player1_name}, {player2_name}',
                'wins': wins,
                'losses': losses,
                'avg_rating': avg_rating
            })
    
    return render_template('view_teams.html', teams=teams_info)



if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000)

