# Mybatis

什么是Mybatis？

Mybatis是一个轻量级的ORM框架（操作数据库的框架）

## 一、SpringBoot整合Mybatis

### 1、引入依赖

```xml
<!--1、引入mysql的驱动包 -->
<dependency>
    <groupId>mysql</groupId>
    <artifactId>mysql-connector-java</artifactId>
    <version>8.0.33</version>
</dependency>
<!--2、用到连接池（druid） -->
<dependency>
    <groupId>com.alibaba</groupId>
    <artifactId>druid-spring-boot-starter</artifactId>
    <version>1.2.14</version>
</dependency>
<!--3、Mybatis的starter-->
<dependency>
    <groupId>org.mybatis.spring.boot</groupId>
    <artifactId>mybatis-spring-boot-starter</artifactId>
    <version>2.3.0</version>
</dependency>
```



完整的pom.xml的内容

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>3.4.0</version>
        <relativePath/> <!-- lookup parent from repository -->
    </parent>
    <groupId>com.hwadee</groupId>
    <artifactId>student-manager-system</artifactId>
    <version>0.0.1-SNAPSHOT</version>
    <name>student-manager-system</name>
    <description>student-manager-system</description>

    <properties>
        <java.version>17</java.version>
    </properties>
    <dependencies>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter</artifactId>
        </dependency>
        <!--配置该系统为一个web项目-->
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-web</artifactId>
        </dependency>

        <dependency>
            <groupId>org.projectlombok</groupId>
            <artifactId>lombok</artifactId>
            <version>1.18.42</version>
        </dependency>


        <!--1、引入mysql的驱动包 -->
        <dependency>
            <groupId>mysql</groupId>
            <artifactId>mysql-connector-java</artifactId>
            <version>8.0.33</version>
        </dependency>
        <!--2、用到连接池（druid） -->
        <dependency>
            <groupId>com.alibaba</groupId>
            <artifactId>druid-spring-boot-starter</artifactId>
            <version>1.2.14</version>
        </dependency>
        <!--3、Mybatis的starter-->
        <dependency>
            <groupId>org.mybatis.spring.boot</groupId>
            <artifactId>mybatis-spring-boot-starter</artifactId>
            <version>3.0.3</version>
        </dependency>


        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-test</artifactId>
            <scope>test</scope>
        </dependency>
    </dependencies>
    <build>
        <plugins>
            <plugin>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-maven-plugin</artifactId>
                <configuration>
                    <excludes>
                        <exclude>
                            <groupId>org.projectlombok</groupId>
                            <artifactId>lombok</artifactId>
                        </exclude>
                    </excludes>
                </configuration>
            </plugin>
        </plugins>
    </build>
</project>
```



### 2、配置application.yml

```yaml
server:
  port: 8081

spring:
  datasource:
    username: root
    password: admin
    url: jdbc:mysql://localhost:3306/sms?useUnicode=true&characterEncoding=utf8&useSSL=false&serverTimezone=UTC
    driver-class-name: com.mysql.cj.jdbc.Driver
    type: com.alibaba.druid.pool.DruidDataSource
```



### 3、Mybatis通过注解的方式访问数据库

```java
package com.hwadee.studentmanagersystem.mapper;

import com.hwadee.studentmanagersystem.entity.SysUser;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Select;

import java.util.List;

@Mapper
public interface SysUserMapper {

    @Select("select * from sys_user")
    List<SysUser> listAll();
}
```



如果需要在注解中传入参数：需要使用 **#{变量名}** 的方式传入参数（只有一个参数的时候，变量名可以随便写）

```java
@Select("select * from sys_user where id=#{id}")
SysUser selectById(Integer id);
```

多参数的情况，要求变量名必须和参数名字要一致。

```java
@Select("select * from sys_user where id=#{id} and username= #{username}")
SysUser selectById(Integer id, String username);

```

```java
// 可以通过@Param注解来修改参数的名字
@Select("select * from sys_user where id=#{userId} and username= #{username}")
SysUser selectById(@Param("userId") Integer id, String username);
```

```java
//当传入的参数为对象时，需要取该对象中的属性值时，直接写该对象中的属性名称即可
@Select("select * from sys_user where id=#{id}")
SysUser selectByConditions(SysUser user);
```



### 4、通过Mapper.xml关联Mapper接口的方式

（1）配置Mybatis的配置信息，在application.yml中添加如下配置

```yaml
mybatis:
  mapper-locations: classpath:mappers/*.xml
```

（2）在resources/mappers文件中添加一个与Mapper接口名字相同的xml文件, 如：SysUserMapper.xml

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.hwadee.studentmanagersystem.mapper.SysUserMapper">
    <!--id 一定要和接口中的方法名称一致, parameterType参数类型要和方法的参数类型一致，返回值类型也要一致-->
    <select id="selectByConditions" parameterType="com.hwadee.studentmanagersystem.entity.SysUser"
     resultType="com.hwadee.studentmanagersystem.entity.SysUser">
        select * from sys_user where id=#{id}
    </select>
</mapper>
```

（3）实体类中的属性名和数据库中的列名不一致会导致我们查询出来的数据中会丢失到这些属性的值，那么我们需要添加一个实体类与数据库的映射关系，在Mapper.xml中添加resultMap，并将查询语句中的resultType修改成resultMap，值为上面定义的resultMap的id

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.hwadee.studentmanagersystem.mapper.SysUserMapper">
	<!--property是指的实体类中的属性，column指的是数据库中的列名 -->
    <resultMap id="userMap" type="com.hwadee.studentmanagersystem.entity.SysUser">
        <id property="id" column="id"/>
        <result property="username" column="username"/>
        <result property="password" column="password"/>
        <result property="email" column="email"/>
        <result property="age" column="age"/>
        <result property="sex" column="sex"/>
        <result property="realName" column="real_name"/>
        <result property="stuNo" column="stu_no"/>
        <result property="createTime" column="create_time"/>
        <result property="updateTime" column="update_time"/>
        <result property="createBy" column="create_by"/>
        <result property="updateBy" column="update_by"/>
    </resultMap>

    <!--id 一定要和接口中的方法名称一致, parameterType参数类型要和方法的参数类型一致，返回值类型也要一致-->
    <select id="selectByConditions" parameterType="com.hwadee.studentmanagersystem.entity.SysUser"
     resultMap="userMap">
        select * from sys_user where id=#{id}
    </select>
</mapper>
```

（4）每次在写parameterType的时候，都要写一长串的字符串，如何去简便的书写呢？

需要在application.yml中配置一下，内容如下：

```yaml
mybatis:
  mapper-locations: classpath:mappers/*.xml
  type-aliases-package: com.hwadee.studentmanagersystem.entity
```

在Mapper.xml中我们就可以直接写类名了

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.hwadee.studentmanagersystem.mapper.SysUserMapper">

    <resultMap id="userMap" type="SysUser">
        <id property="id" column="id"/>
        <result property="username" column="username"/>
        <result property="password" column="password"/>
        <result property="email" column="email"/>
        <result property="age" column="age"/>
        <result property="sex" column="sex"/>
        <result property="realName" column="real_name"/>
        <result property="stuNo" column="stu_no"/>
        <result property="createTime" column="create_time"/>
        <result property="updateTime" column="update_time"/>
        <result property="createBy" column="create_by"/>
        <result property="updateBy" column="update_by"/>
    </resultMap>

    <!--id 一定要和接口中的方法名称一致, parameterType参数类型要和方法的参数类型一致，返回值类型也要一致-->
    <select id="selectByConditions" parameterType="SysUser"
     resultMap="userMap">
        select * from sys_user where id=#{id}
    </select>
</mapper>
```

## 二、mapper文件的常用标签

1、<mapper namespace="">

该标签中有一个重要的属性，namespace，这个namespace的值一般要求是与该Mapper.xml对应的Mapper接口的类的全路径。例如：com.hwadee.mybatis.mapper.UserMapper

2、<select id="" resultType="" parameterType="">查询sql语句</select>

select标签是用来编写查询sql语句的，其中有三个属性，id表示该sql语句的唯一标识， resultType是指返回值的类型， parameterType是指传入参数的类型。其中sql语句中要去获取parameterType传入参数的值，是用#{变量名}（如果传入参数是一个自定义对象，那么可以#{对象属性}）

3、<update id="" parameterType="">更新的sql语句</update>

属性同上

4、<delete id="" parameterType=""> 删除的sql语句</delete>

属性同上

5、<insert id="" parameterType=""> 新增的sql语句</delete>

属性同上



## 三、动态sql

1、if标签

格式如下：

<if test="条件 [and|or 条件...]">

if标签是用来判断使用，其中test属性的值就是判定条件，判定条件可以是多个条件表达式，他们之间与关系是使用and，或关系是使用or来连接。

例如：

```xml
    <select id="selectByConditions" parameterType="user" resultType="user">
        select id, username, password, email, age from user where 1=1
        <if test="id != null">
           and id = #{id}
        </if>
        <if test="username != null and username != ''">
            and username = #{username}
        </if>
        <if test="password != null and password != ''">
            and password = #{password}
        </if>
        <if test="email != null and email != ''">
            and email = #{email}
        </if>
        <if test="age != null">
            and age = #{age}
        </if>
    </select>
```

2、sql标签

格式：<sql id=""> 公共sql</sql>

sql标签是用来抽取sql语句中的公共部分，用来统一管理公共部分的sql语句，可以提高后期的维护效率。

要引入sql标签提取出来的sql语句，可以使用<include refid="sqlID"></include>

具体示例如下：

```xml
    <sql id="selectSql">
        select id, username, password, email, age from user
    </sql>

    <select id="getUserById" resultType="com.hwadee.mybatis.pojo.User" parameterType="int">
        <include refid="selectSql"></include> where id = #{id}
    </select>
```

3、where标签

格式： <where> 条件</where>

where标签是用来去掉条件中多余的and或者or的标签，并且也会添加上一个where关键字。

例如：

```xml
    <sql id="selectSql">
        select id, username, password, email, age from user
    </sql>
    <select id="selectByConditions" parameterType="user" resultType="user">
        <include refid="selectSql"></include>
        <where>
            <if test="id != null">
               and id = #{id}
            </if>
            <if test="username != null and username != ''">
                and username = #{username}
            </if>
            <if test="password != null and password != ''">
                and password = #{password}
            </if>
            <if test="email != null and email != ''">
                and email = #{email}
            </if>
            <if test="age != null">
                and age = #{age}
            </if>
        </where>
    </select>
```

4、<foreach> 循环遍历

格式如下例子：

```xml
 <select id="selectByIds" parameterType="long" resultMap="userMap">
    select * from sys_user
    where id in <foreach collection="ids" item="id" open="(" separator="," close=")">#{id}</foreach>
  </select>
```

## 四、开启mybatis的sql日志打印

在application.yml中添加如下配置

```yaml
logging:
  level:
    org.mybatis: DEBUG
    org.mybatis.spring: DEBUG
    com.hwadee.managersystem: DEBUG
```

## 五、一对多查询

使用<collection>来进行连接，例如：

```xml
<resultMap id="userOrderMap" type="SysUser">
    <id property="id" column="id"/>
    <result property="username" column="username"/>
    <result property="password" column="password"/>
    <result property="email" column="email"/>
    <result property="age" column="age"/>
    <result property="sex" column="sex"/>
    <result property="realName" column="real_name"/>
    <result property="stuNo" column="stu_no"/>
    <result property="createTime" column="create_time"/>
    <result property="updateTime" column="update_time"/>
    <result property="createBy" column="create_by"/>
    <result property="updateBy" column="update_by"/>
    <collection property="sysOrders" ofType="SysOrder">
        <id property="orderId" column="order_id"/>
        <result property="orderCode" column="order_code"/>
        <result property="totalMoney" column="total_money"/>
        <result property="remark" column="remark"/>
        <result property="userId" column="user_id"/>
    </collection>
</resultMap>

<select id="selectAllUserOrdersDetails" resultMap="userOrderMap">
    select u.*, o.* from sys_user u LEFT JOIN sys_order o ON u.id=o.user_id
</select>
```

## 六、缓存机制

Mybatis中的缓存：一级缓存和二级缓存。

缓存的意义是什么？

提高我们的效率，减少与数据库打交道。

### 1、一级缓存

MyBatis 的 **一级缓存是基于 SqlSession 的本地缓存**，默认开启。它只对当前会话有效，当会话关闭或提交后缓存失效。



### 2、二级缓存

MyBatis 的 **二级缓存是跨 SqlSession 的共享缓存**，作用范围是 `namespace`，即每个 Mapper 接口拥有独立的缓存空间。

默认情况下，MyBatis 不开启二级缓存，需要手动配置。

二级缓存，缓存的对象一定要是可以序列化的（Java实体类必须要实现Serializable 接口）。

配置：

在application.yml中开启二级缓存

```yaml
mybatis:
  mapper-locations: classpath:mappers/*.xml
  type-aliases-package: com.hwadee.studentmanagersystem.entity
  configuration:
    cache-enabled: true  # 开启二级缓存
```

需要在Mapper.xml中开启二级缓存，在xml中添加<cache/>标签即可。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.hwadee.studentmanagersystem.mapper.SysUserMapper">
    <cache/>
    <resultMap id="userMap" type="SysUser">
        <id property="id" column="id"/>
        <result property="username" column="username"/>
        <result property="password" column="password"/>
        <result property="email" column="email"/>
        <result property="age" column="age"/>
        <result property="sex" column="sex"/>
        <result property="realName" column="real_name"/>
        <result property="stuNo" column="stu_no"/>
        <result property="createTime" column="create_time"/>
        <result property="updateTime" column="update_time"/>
        <result property="createBy" column="create_by"/>
        <result property="updateBy" column="update_by"/>
    </resultMap>

    <sql id="userColumns">
        id, username, password, email, age, sex, real_name, stu_no, create_time, update_time, create_by, update_by
    </sql>

    <sql id="userValues">
        #{id}, #{username}, #{password}, #{email}, #{age}, #{sex}, #{realName}, #{stuNo}, #{createTime}, #{updateTime}, #{createBy}, #{updateBy}
    </sql>

    <sql id="orderColumns"> order_id, order_code, user_id, total_money, remark</sql>

    <!--id 一定要和接口中的方法名称一致, parameterType参数类型要和方法的参数类型一致，返回值类型也要一致-->
    <select id="selectByConditions" parameterType="SysUser" resultMap="userMap">
        select <include refid="userColumns"/> from sys_user
        <where>
            <if test="id != null">
                and id=#{id}
            </if>
            <if test="username != null and username != ''">
                and username = #{username}
            </if>
        </where>
    </select>


    <select id="selectByIds" parameterType="long" resultMap="userMap">
        select * from sys_user
        where id in <foreach collection="ids" item="id" open="(" separator="," close=")">#{id}</foreach>
    </select>

    <resultMap id="userOrderMap" type="SysUser">
        <id property="id" column="id"/>
        <result property="username" column="username"/>
        <result property="password" column="password"/>
        <result property="email" column="email"/>
        <result property="age" column="age"/>
        <result property="sex" column="sex"/>
        <result property="realName" column="real_name"/>
        <result property="stuNo" column="stu_no"/>
        <result property="createTime" column="create_time"/>
        <result property="updateTime" column="update_time"/>
        <result property="createBy" column="create_by"/>
        <result property="updateBy" column="update_by"/>
        <collection property="sysOrders" ofType="SysOrder">
            <id property="orderId" column="order_id"/>
            <result property="orderCode" column="order_code"/>
            <result property="totalMoney" column="total_money"/>
            <result property="remark" column="remark"/>
            <result property="userId" column="user_id"/>
        </collection>
    </resultMap>

    <select id="selectAllUserOrdersDetails" resultMap="userOrderMap">
        select u.*, o.* from sys_user u LEFT JOIN sys_order o ON u.id=o.user_id
    </select>


    <insert id="save" parameterType="sysUser">
        insert into sys_user(<include refid="userColumns"/>)
        values (<include refid="userValues"/>)
    </insert>

    <!--
    insert into sys_user(列名) values(),(),(),(),(),(),(),(),(),(),(),()
    -->
    <insert id="saveList" parameterType="sysUser">
        insert into sys_user(<include refid="userColumns"/>)
        values
        <foreach collection="users" item="u" separator="," >
        (#{u.id}, #{u.username}, #{u.password}, #{u.email}, #{u.age}, #{u.sex}, #{u.realName}, #{u.stuNo}, #{u.createTime}, #{u.updateTime}, #{u.createBy}, #{u.updateBy})
        </foreach>
    </insert>


    <update id="updateById" parameterType="sysUser">
        update sys_user
        <set>
            <if test="username != null and username != ''">
                username=#{username},
            </if>
            <if test="password != null and password != ''">
                password=#{password},
            </if>
            <if test="email != null and email != ''">
                email=#{email},
            </if>
            <if test="age != null">
                age=#{age},
            </if>
            <if test="sex != null">
                sex=#{sex},
            </if>
            <if test="realName != null and realName != ''">
                real_name=#{realName},
            </if>
            <if test="stuNo != null and stuNo != ''">
                stu_no=#{stuNo},
            </if>
            <if test="updateTime != null">
                update_time=#{updateTime},
            </if>
            <if test="updateBy != null">
                update_by=#{updateBy},
            </if>
        </set>
        where id=#{id}
    </update>

<!--  ${id}
  #{} :简单认为#{}会给变量添加一个''，其实这个里面做了一些处理，（防止sql注入） ？

  ${}:原样的将值放在指定的位置


  -->
    <delete id="deleteById" parameterType="long">
        delete from sys_user where id=#{id}
    </delete>


</mapper>
```

需要查询的实体类实现序列化接口

```java
package com.hwadee.studentmanagersystem.entity;

import lombok.Data;
import lombok.ToString;

import java.io.Serializable;
import java.util.Date;
import java.util.List;

/**
 * @author yang hui
 * @date 2026/3/4
 * @desc
 */
@Data
@ToString
public class SysUser implements Serializable {
    private Long id;
    private String username;
    private String password;
    private String email;
    private Integer sex;
    private Integer age;
    private String realName;
    private String stuNo;
    private Date createTime;
    private Long createBy;
    private Date updateTime;
    private Long updateBy;
    private List<SysOrder> sysOrders;
}
```

# 导入成mybatis-plus

要修改配置文件为

```
mybatis-plus:
  #xml path
  mapper-locations: classpath:mapper/*.xml
  #xml
  type-aliases-package: org.example.manager.entity
  configuration:
    # ????????????user_pic ? ???userPic?
    map-underscore-to-camel-case: true
```