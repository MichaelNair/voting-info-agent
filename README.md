This is a design and implementation of an MCP agent that explains to people how to vote.
This tool will be focused on state and national elections for now.

This will be using openai's gpt-5.1 model.

Capabilities:
- Tell a person there closest polling location
- Tell a person the date of the election
- Tell a person the times a given polling location opens and closes

- Tell a person steps to do early-in-person voting in there state
- Tell a person dates early-in-person voting starts and ends
- Tell a person location for early-in-person voting
- Tell a person times early-in-person voting locations are open

- Tell a person how to request a mail-in-ballot in there state
- Tell a person the date by which they have to request the ballot (may require extrapolation)
- Tell a person the date by which they have to send the ballot
- Tell a person the date by which the ballot has to arrive to be counted (I think this may be the way some states do it)

- Figure out what State a given address is in
- Figure out what district/presinct a given address it in
- Figure out what today's date is
- Figure out when the nearest election is for a given state

Tools:
- get_current_date()
- get_district_from_address()
    - Gets the voting disctrict or precident for a given address
    - There may already be a tool out where that can do this
    - I know the district's change so I will have to make sure everything is up to date
    - probably will have to pull from different data sources for different states
- get_nearest_election()
    - Will have to make an election object
    - again this will probably have to: 1. figure out what state and 2. pull from some state spesific source for the information we want
    - Election object should be
        - Date early-in-person voting starts
        - Date early-in-person voting ends
        - Last Date to request mail-in-ballot
        - Last Date to send mail-in-ballot
        - Date mail-in-ballot much be received by
        - Date of election
        - State
        - Voting district or presinct
        - most common time zone of district or presinct
        - List of race objects on the ballot
        - List of candiates for each race object
        - candidate object 
            - candidate name
            - candidate party
        - List of Ballot questions (I think ballot questions are always yes or no)
        - ballot-question object
            - A list of possible answers which should all be strings
- get_instructions_from_state_website()
    - get voting instructions from a state website for a given type of voting
    - May want to save the url for some states
    - Arguments: state, election_date, type_of_voting (election_day, early_in_person, mail_in_ballot)
- find_nearest_polling_location()
    - arguments: address, disctrict/presinct, election_date
    - find the nearest polling location for that address
    - There may already be an API for this for some states
    - If not get a list of all the polling locations for the election_date and compare each using google maps API


MCP Diagram
![alt text](./diagrams/mcp_sequence_template_20251118_173118.png)


