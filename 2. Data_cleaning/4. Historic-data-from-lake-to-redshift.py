import boto3
import sys
from awsglue.transforms import *
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.sql.functions import *
from pyspark.sql.types import DateType
from pyspark.sql.functions import col

## @params: [JOB_NAME]
args = getResolvedOptions(sys.argv, ['JOB_NAME'])

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args['JOB_NAME'], args)

s3_client = boto3.client('s3')
bucket_name = 'datalake-rawzone-group1dbda-8sept'
#prefix = 'your_prefix/'  # Optional: specify a prefix if your files are in a specific directory

article_response = s3_client.list_objects_v2(Bucket=bucket_name,Prefix='Articles/Articles_historic/')
objects_article = article_response['Contents']
objects_article.sort(key=lambda x: x['LastModified'], reverse=True)
path_article="s3://{}".format(bucket_name)+"/"+objects_article[0]['Key']

customer_response = s3_client.list_objects_v2(Bucket=bucket_name,Prefix='Customers/Customers_historic/')
objects_customer = customer_response['Contents']
objects_customer.sort(key=lambda x: x['LastModified'], reverse=True)
path_customer="s3://{}".format(bucket_name)+"/"+objects_customer[0]['Key']

transaction_response = s3_client.list_objects_v2(Bucket=bucket_name,Prefix='Transactions/Transactions_historic/')
objects_transaction = transaction_response['Contents']
objects_transaction.sort(key=lambda x: x['LastModified'], reverse=True)
path_transaction="s3://{}".format(bucket_name)+"/"+objects_transaction[0]['Key']

customers_df = spark.read.parquet(path_customer)
article_df = spark.read.parquet(path_article)
transaction_df = spark.read.parquet(path_transaction)

def Customer_clean(customers_df):
    # Calculate the median of the 'age' column
    median_age = customers_df.approxQuantile("age", [0.5], 0.0)[0]

    # use the withColumn method to add, modify, or rename columns in a DataFrame
    # col is a function from the pyspark.sql.functions module that allows you to reference DataFrame columns by name.
    # Fill null values in 'age' column with the calculated median
    customers_df = customers_df.withColumn("age", when(col("age").isNull(), median_age).otherwise(col("age")))

    # Dropping 'FN' Column
    customers_df=customers_df.drop('FN')

    # Convert 'Active' column to string and fill missing values with '0'
    customers_df = customers_df.withColumn("Active", when(col("Active").isNull(), "0").otherwise(col("Active").cast("string")))

    #Replacing 'Not available' values with Mode value that is with 'Active' status
    customers_df = customers_df.withColumn("club_member_status", when(col("club_member_status").isNull(), "ACTIVE").otherwise(col("club_member_status")))

    # Replace 'None' and 'NONE' values with 'Never' in 'fashion_news_frequency' column
    customers_df=customers_df.withColumn("fashion_news_frequency",when(col("fashion_news_frequency").isin("None","NONE"),"Never").otherwise(col("fashion_news_frequency")))

    #In fashion_news_frequency 16009 values are not availbale (null) and people those who are not receiving news are 877711 ('NONE') and 
    # those who are receiving news regularly are 477416 ('Regularly)'. 
    # hence we have replaced null values with 'Never' values 
    # NONE means not receving fashion news
    customers_df = customers_df.withColumn("fashion_news_frequency", when(col("fashion_news_frequency").isNull(), "Never").otherwise(col("fashion_news_frequency")))

    # Define the age ranges and labels
    age_bins = [0, 22, 38, 54, 73, 100]
    age_labels = ['Gen-Z', 'Millennials', 'Gen-X', 'Boomers', 'Silent']

    # Add a new column 'age_group' based on age_bins and age_labels
    customers_df = customers_df.withColumn("age_group",
                        when((col("age") >= age_bins[0]) & (col("age") < age_bins[1]), age_labels[0])
                       .when((col("age") >= age_bins[1]) & (col("age") < age_bins[2]), age_labels[1])
                       .when((col("age") >= age_bins[2]) & (col("age") < age_bins[3]), age_labels[2])
                       .when((col("age") >= age_bins[3]) & (col("age") < age_bins[4]), age_labels[3])
                       .otherwise(age_labels[4]))

    # Changing column name from 'Active' to 'active_status'
    customers_df = customers_df.withColumnRenamed('Active','active_status')
    # Changing age from double to int
    customers_df = customers_df.withColumn("age", col("age").cast("int"))
    
    return customers_df

def Transaction_clean(transaction_df):
  
    # Convert the string column to TimestampType
    transaction_df = transaction_df.withColumn('t_dat', col('t_dat').cast(DateType()))
    # date_format(col('t_dat'), 'EEEE'): Here, you're using the pattern 'EEEE', which represents the full day name (e.g., "Monday", "Tuesday").
    # "t_dat" column using the pattern 'MMMM', which represents the full month name.
    # quarter(col('t_dat')): The quarter function calculates the quarter of the year (1, 2, 3, or 4) corresponding to the date in the "t_dat" column.

    transaction_df = transaction_df.withColumn("month", date_format(col('t_dat'),'MMMM'))
    transaction_df = transaction_df.withColumn("day", date_format(col('t_dat'),'EEEE'))
    transaction_df = transaction_df.withColumn("quarter", quarter(col('t_dat')))
    transaction_df = transaction_df.withColumn("year", year(col("t_dat")))
    
    mode_df = transaction_df.groupBy("sales_channel_id").count().orderBy(col("count").desc()).limit(1)
    mode = mode_df.select("sales_channel_id").collect()[0][0]
    # The collect() function is used to retrieve the data from the DataFrame and convert it into a list of rows.

    transaction_df = transaction_df.withColumn("sales_channel_id", when(col("sales_channel_id").isNull(), mode).otherwise(col("sales_channel_id")))
    
    return transaction_df

def Articles_clean(article_df):
    article_df = article_df.drop('detail_desc')
    return article_df

entity_list = [customers_df,article_df,transaction_df]  

for item in entity_list:
    if item==customers_df:
        customers_df = Customer_clean(item)
    elif item==article_df:
        article_df = Articles_clean(item)
    else:
        transaction_df = Transaction_clean(item)

	
transaction_df = transaction_df.repartition(8)

joindf = transaction_df.join(broadcast(customers_df),'customer_id')
final_join = joindf.join(broadcast(article_df),'article_id')

aws_creds={"aws_access_key_id":"ASIAVROON6L3UWIKSE6I","aws_secret_access_key":"Q1HibL1mpPe6XJ+qSbhSNbcWxKJLZr+YxgEl3irO","aws_session_token":"FwoGZXIvYXdzENX//////////wEaDBKFWGZjz3xTMpdj5iLMAQygg5uui1qVh3OKitFYdn4eTo3gbTZVDjB+LjxT+JShq/fvd4+SGnuxrSjSThF3nM8QwVSKCM1zcv6CngzaTKqiruN77W/BQotKuxG/o1QVVkPi2RVa+nXB+gEUmSujCsxDGEPGxi7u9p5ZuG83kc0en0AjuQ0ctbjPFZBuR2xPY1fcBk0uaN97ZdoQJYGViP0o45eYmKrpUM7Yi3eMxQnGT+9RP5aaB1xFseFU6nobadD8I/1IJJNd83I5Rv0Pb70SQvK5SBUMS16bkyirnuqnBjItGU16Bl3SKrpdzKtCmooXs60vhUtc3bSFyQYyxOPiakBlM6QbON9ZXsR4BElD","region_name":"us-east-1"}

# Create Redshift Boto3 Client

redshift_client = boto3.client('redshift',aws_access_key_id=aws_creds['aws_access_key_id'],
aws_secret_access_key=aws_creds['aws_secret_access_key'],
aws_session_token=aws_creds['aws_session_token'],
region_name=aws_creds['region_name'])

# Specify the Redshift cluster identifier
cluster_identifier = 'tf-redshift-cluster'

# Describe the Redshift cluster to get its endpoint information
response = redshift_client.describe_clusters(ClusterIdentifier=cluster_identifier)
#print(response['Clusters']['MasterUsername'])
# Extract the cluster endpoint information
cluster_endpoint = response['Clusters'][0]['Endpoint']['Address']
cluster_port = response['Clusters'][0]['Endpoint']['Port']

# Print the Redshift cluster endpoint
url=f"jdbc:redshift://{cluster_endpoint}:{cluster_port}/dev"

redshift_username = "awsuser"
redshift_password = "HM27march99"

redshift_properties = {
    "user": redshift_username,
    "password": redshift_password
}


iam_client = boto3.client('iam',aws_access_key_id=aws_creds['aws_access_key_id'],
aws_secret_access_key=aws_creds['aws_secret_access_key'],
aws_session_token=aws_creds['aws_session_token'],region_name=aws_creds['region_name'])

role_name = 'LabRole'
response = iam_client.get_role(RoleName=role_name)
role_arn = response['Role']['Arn']

redshift_temp_dir = "s3://redshift-logs-group1dbda-8sept/red-logs"

final_join.write \
    .format("io.github.spark_redshift_community.spark.redshift") \
    .option("url", url) \
    .option("dbtable", "warehouse") \
    .option("tempdir", redshift_temp_dir) \
    .option("aws_iam_role", role_arn) \
    .option("user", redshift_username) \
    .option("password", redshift_password) \
    .mode("append") \
    .save()

customers_df.write \
    .format("io.github.spark_redshift_community.spark.redshift") \
    .option("url", url) \
    .option("dbtable", "customers") \
    .option("tempdir", redshift_temp_dir) \
    .option("aws_iam_role", role_arn) \
    .option("user", redshift_username) \
    .option("password", redshift_password) \
    .mode("overwrite") \
    .save()

article_df.write \
    .format("io.github.spark_redshift_community.spark.redshift") \
    .option("url", url) \
    .option("dbtable", "articles") \
    .option("tempdir", redshift_temp_dir) \
    .option("aws_iam_role", role_arn) \
    .option("user", redshift_username) \
    .option("password", redshift_password) \
    .mode("overwrite") \
    .save()

transaction_df.write \
    .format("io.github.spark_redshift_community.spark.redshift") \
    .option("url", url) \
    .option("dbtable", "transactions") \
    .option("tempdir", redshift_temp_dir) \
    .option("aws_iam_role", role_arn) \
    .option("user", redshift_username) \
    .option("password", redshift_password) \
    .mode("overwrite") \
    .save()



job.commit()