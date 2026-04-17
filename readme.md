# Flickr MCP Server

Write a MCP server that can be used to search for photos on Flickr and return the results to the client.
Author: Eric Wettstein, Mr. E Photos, ejwettstein on flickr

## MCP Server
The MCP server will work with the command line python scripts.  The scripts are located in the `scripts` directory.  The scripts are used to search for photos on Flickr and return the results to the client.  The scripts are used to search for photos on Flickr and return the results to the client.

## Python Scripts
The scripts will use the Flickr API to search for photos on Flickr and return the results to the client.  The scripts will use the Flickr API to manage an individual photos, albums and followers.

## API Keys
API keys are stored in the `.env` file.

## Database
Database is stored in the `.db` file.  It is a sqlite database. It is used to map a users flickr account.

## Features for CLI
1. Login to flickr (API and OA  uth)
1. Logout of flickr
1. Get my public photos (with pagination): title, description, date taken, date uploaded, url, tags, comments, favorites, notes, licenses, sizes, exif, history, geo, people, related, similar, stats
1. Save data to the database
1. Refresh this list from flickr (this will update the database with new photos and update existing photos)
1. Update a photo title, description and tags

## Features for the MCP Server
1. Login to flickr (API and OA  uth)
1. get recent photos from the database
1. update the photo title, description and tags


## Resources
* https://www.flickr.com/services/api/
