#Python Standard Library
import base64
import json
from urllib.parse import unquote

#Extra modules
import boto3
import stark_scrypt as scrypt

ddb = boto3.client('dynamodb')

#######
#CONFIG
ddb_table   = "[[STARK_DDB_TABLE_NAME]]"
default_sk  = "STARK|user|info"
sort_fields = ["Username", ]
page_limit  = 10

def lambda_handler(event, context):

    #Get request type
    request_type = event.get('queryStringParameters',{}).get('rt','')

    if request_type == '':
        ########################
        #Handle non-GET requests

        #Get specific request method
        method  = event.get('requestContext').get('http').get('method')

        if event.get('isBase64Encoded') == True :
            payload = json.loads(base64.b64decode(event.get('body'))).get('STARK_User',"")
        else:    
            payload = json.loads(event.get('body')).get('STARK_User',"")

        data    = {}

        if payload == "":
            return {
                "isBase64Encoded": False,
                "statusCode": 400,
                "body": json.dumps("Client payload missing"),
                "headers": {
                    "Content-Type": "application/json",
                }
            }
        else:
            data['pk'] = payload.get('Username')
            data['orig_pk'] = payload.get('orig_Username','')
            data['sk'] = payload.get('sk', '')
            if data['sk'] == "":
                data['sk'] = default_sk
            data['Full_Name'] = payload.get('Full_Name','')
            data['Nickname'] = payload.get('Nickname','')
            data['Password_Hash'] = payload.get('Password_Hash','')
            data['Role'] = payload.get('Role','')
        ListView_index_values = []
        for field in sort_fields:
            ListView_index_values.append(payload.get(field))
        data['STARK-ListView-sk'] = "|".join(ListView_index_values)

        if method == "DELETE":
            response = delete(data)

        elif method == "PUT":

            #We can't update DDB PK, so if PK is different, we need to do ADD + DELETE
            if data['orig_pk'] == data['pk']:
                response = edit(data)
            else:
                response   = add(data)
                data['pk'] = data['orig_pk']
                response   = delete(data)

        elif method == "POST":
            response = add(data)

        else:
            return {
                "isBase64Encoded": False,
                "statusCode": 400,
                "body": json.dumps("Could not handle API request"),
                "headers": {
                    "Content-Type": "application/json",
                }
            }

    else:
        ####################
        #Handle GET requests
        if request_type == "all":
            #check for submitted token
            lv_token = event.get('queryStringParameters',{}).get('nt', None)
            if lv_token != None:
                lv_token = unquote(lv_token)
                lv_token = json.loads(lv_token)

            items, next_token = get_all(default_sk, lv_token)

            response = {
                'Next_Token': json.dumps(next_token),
                'Items': items
            }

        elif request_type == "report":
            response = report(default_sk)

        elif request_type == "detail":

            pk = event.get('queryStringParameters').get('Username','')
            sk = event.get('queryStringParameters').get('sk','')
            if sk == "":
                sk = default_sk

            response = get_by_pk(pk, sk)
        else:
            return {
                "isBase64Encoded": False,
                "statusCode": 400,
                "body": json.dumps("Could not handle GET request - unknown request type"),
                "headers": {
                    "Content-Type": "application/json",
                }
            }

    return {
        "isBase64Encoded": False,
        "statusCode": 200,
        "body": json.dumps(response),
        "headers": {
            "Content-Type": "application/json",
        }
    }

def report(sk):
    #FIXME: THIS IS A STUB, WILL NEED TO BE UPDATED WITH
    #   ENHANCED LISTVIEW LOGIC LATER WHEN WE ACTUALLY IMPLEMENT REPORTING

    response = ddb.query(
        TableName=ddb_table,
        IndexName="STARK-ListView-Index",
        Select='ALL_ATTRIBUTES',
        ReturnConsumedCapacity='TOTAL',
        KeyConditionExpression='sk = :sk',
        ExpressionAttributeValues={
            ':sk' : {'S' : sk}
        }
    )

    raw = response.get('Items')

    #Map to expected structure
    #FIXME: this is duplicated code, make this DRY by outsourcing the mapping to a different function.
    items = []
    for record in raw:
        item = {}
        item['Username'] = record.get('pk', {}).get('S','')
        item['sk'] = record.get('sk',{}).get('S','')
        item['Full_Name'] = record.get('Full_Name',{}).get('S','')
        item['Nickname'] = record.get('Nickname',{}).get('S','')
        item['Password_Hash'] = record.get('Password_Hash',{}).get('S','')
        item['Role'] = record.get('Role',{}).get('S','')
        items.append(item)

    return items

def get_all(sk, lv_token=None):

    if lv_token == None:
        response = ddb.query(
            TableName=ddb_table,
            IndexName="STARK-ListView-Index",
            Select='ALL_ATTRIBUTES',
            Limit=page_limit,
            ReturnConsumedCapacity='TOTAL',
            KeyConditionExpression='sk = :sk',
            ExpressionAttributeValues={
                ':sk' : {'S' : sk}
            }
        )
    else:
        response = ddb.query(
            TableName=ddb_table,
            IndexName="STARK-ListView-Index",
            Select='ALL_ATTRIBUTES',
            Limit=page_limit,
            ExclusiveStartKey=lv_token,
            ReturnConsumedCapacity='TOTAL',
            KeyConditionExpression='sk = :sk',
            ExpressionAttributeValues={
                ':sk' : {'S' : sk}
            }
        )

    raw = response.get('Items')

    #Map to expected structure
    #FIXME: this is duplicated code, make this DRY by outsourcing the mapping to a different function.
    items = []
    for record in raw:
        item = {}
        item['Username'] = record.get('pk', {}).get('S','')
        item['sk'] = record.get('sk',{}).get('S','')
        item['Full_Name'] = record.get('Full_Name',{}).get('S','')
        item['Nickname'] = record.get('Nickname',{}).get('S','')
        item['Role'] = record.get('Role',{}).get('S','')

        items.append(item)
    #NOTE: We explicitly left out the password hash. Since this is the generic "get all records" function, there's really no
    #   legitimate reason to get something as sensitive as the passwordh hash. Functionality that actually has to mass list
    #   users alongside their password hashes will have to use a function specifically made for that. Safety first.

    #Get the "next" token, pass to calling function. This enables a "next page" request later.
    next_token = response.get('LastEvaluatedKey')

    return items, next_token

def get_by_pk(pk, sk):
    response = ddb.query(
        TableName=ddb_table,
        Select='ALL_ATTRIBUTES',
        KeyConditionExpression="#pk = :pk and #sk = :sk",
        ExpressionAttributeNames={
            '#pk' : 'pk',
            '#sk' : 'sk'
        },
        ExpressionAttributeValues={
            ':pk' : {'S' : pk },
            ':sk' : {'S' : sk }
        }
    )

    raw = response.get('Items')

    #FIXME: Mapping is duplicated code, make this DRY
    #Map to expected structure
    items = []
    for record in raw:
        item = {}
        item['Username'] = record.get('pk', {}).get('S','')
        item['sk'] = record.get('sk',{}).get('S','')
        item['Full_Name'] = record.get('Full_Name',{}).get('S','')
        item['Nickname'] = record.get('Nickname',{}).get('S','')
        item['Role'] = record.get('Role',{}).get('S','')
        items.append(item)
    #NOTE: We explicitly left out the password hash. Functionality that requires the user record along with the password hash should use 
    #       a specialized function instead of the generic "get" function.

    return items

def delete(data):
    pk = data.get('pk','')
    sk = data.get('sk','')

    response = ddb.delete_item(
        TableName=ddb_table,
        Key={
            'pk' : {'S' : pk},
            'sk' : {'S' : sk}
        }
    )

    return "OK"

def edit(data):                
    pk = data.get('pk', '')
    sk = data.get('sk', '')
    Full_Name = str(data.get('Full_Name', ''))
    Nickname = str(data.get('Nickname', ''))
    Password_Hash = str(data.get('Password_Hash', ''))
    Role = str(data.get('Role', ''))

    UpdateExpressionString = "SET #Full_Name = :Full_Name, #Nickname = :Nickname, #Role = :Role" 
    ExpressionAttributeNamesDict = {
        '#Full_Name' : 'Full_Name',
        '#Nickname' : 'Nickname',
        '#Role' : 'Role',
    }
    ExpressionAttributeValuesDict = {
        ':Full_Name' : {'S' : Full_Name },
        ':Nickname' : {'S' : Nickname },
        ':Role' : {'S' : Role },
    }

    #If Password_Hash is not an empty string, this means it's a password reset request.
    if Password_Hash != '':
        UpdateExpressionString += ", #Password_Hash = :Password_Hash"
        ExpressionAttributeNamesDict['#Password_Hash'] = 'Password_Hash'
        ExpressionAttributeValuesDict[':Password_Hash'] = {'S': scrypt.create_hash(Password_Hash)}

    #If STARK-ListView-sk is part of the data payload, it should be added to the update expression
    if data.get('STARK-ListView-sk','') != '':
        UpdateExpressionString += ", #STARKListViewsk = :STARKListViewsk"
        ExpressionAttributeNamesDict['#STARKListViewsk']  = 'STARK-ListView-sk'
        ExpressionAttributeValuesDict[':STARKListViewsk'] = {'S' : data['STARK-ListView-sk']}

    response = ddb.update_item(
        TableName=ddb_table,
        Key={
            'pk' : {'S' : pk},
            'sk' : {'S' : sk}
        },
        UpdateExpression=UpdateExpressionString,
        ExpressionAttributeNames=ExpressionAttributeNamesDict,
        ExpressionAttributeValues=ExpressionAttributeValuesDict
    )

    return "OK"

def add(data):
    pk = data.get('pk', '')
    sk = data.get('sk', '')
    Full_Name = str(data.get('Full_Name', ''))
    Nickname = str(data.get('Nickname', ''))
    Password_Hash = str(data.get('Password_Hash', ''))
    Role = str(data.get('Role', ''))

    item={}
    item['pk'] = {'S' : pk}
    item['sk'] = {'S' : sk}
    item['Full_Name'] = {'S' : Full_Name}
    item['Nickname'] = {'S' : Nickname}
    item['Password_Hash'] = {'S' : scrypt.create_hash(Password_Hash)}
    item['Role'] = {'S' : Role}

    if data.get('STARK-ListView-sk','') != '':
        item['STARK-ListView-sk'] = {'S' : data['STARK-ListView-sk']}

    response = ddb.put_item(
        TableName=ddb_table,
        Item=item,
    )

    return "OK"
